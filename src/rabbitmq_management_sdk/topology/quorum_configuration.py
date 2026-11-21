"""Resolve captured quorum delivery configuration without policy regex matching."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast

from rabbitmq_management_sdk.exceptions import TopologyParseError
from rabbitmq_management_sdk.topology.models import (
    ConfigurationEvidence,
    ConfigurationSource,
    ConfigurationStatus,
    ConfigurationUncertainty,
    ConfigurationValue,
    QuorumDeliveryConfiguration,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from rabbitmq_management_sdk.resources.v4.admin.schemas.export_response import (
        ClusterDefinitionsResponse,
        ClusterExportQueue,
    )
    from rabbitmq_management_sdk.resources.v4.queues.schemas.queue_response import QueueResponse

type _Value = ConfigurationValue[str | int]
type _Merge = Literal["left", "right", "min", "max", "limit"]

_OPERATOR_KEYS = frozenset(
    {"delayed-retry-type", "delayed-retry-min", "delayed-retry-max", "delivery-limit", "overflow"}
)
_SCOPES = frozenset({"all", "queues", "quorum_queues"})
_MINOR_42 = 2
_MINOR_43 = 3
_POLICY_MERGES: dict[str, _Merge] = {
    "delayed-retry-type": "right",
    "delayed-retry-min": "max",
    "delayed-retry-max": "min",
    "delivery-limit": "limit",
    "overflow": "right",
}


def _known(value: str | int, evidence: tuple[ConfigurationEvidence, ...] = ()) -> _Value:
    return ConfigurationValue(value, ConfigurationStatus.KNOWN, evidence, ())


def _state(status: ConfigurationStatus, evidence: tuple[ConfigurationEvidence, ...] = ()) -> _Value:
    return ConfigurationValue(None, status, evidence, ())


def _unknown(
    reason: ConfigurationUncertainty,
    evidence: tuple[ConfigurationEvidence, ...] = (),
    additional: tuple[ConfigurationUncertainty, ...] = (),
) -> _Value:
    return ConfigurationValue(None, ConfigurationStatus.UNKNOWN, evidence, (reason, *additional))


def _entry(values: Mapping[str, object], key: str, source: ConfigurationSource, name: str | None = None) -> _Value:
    if key not in values:
        return _state(ConfigurationStatus.UNSET)
    value = values[key]
    if value is not None and type(value) not in (str, int):
        return _unknown(ConfigurationUncertainty.INVALID_VALUE)
    scalar = cast("str | int | None", value)
    evidence = (ConfigurationEvidence(source, key, scalar, name),)
    if scalar is None:
        return _unknown(ConfigurationUncertainty.CAPTURED_NULL, evidence)
    return _known(scalar, evidence)


def _merge(left: _Value, right: _Value, mode: _Merge) -> _Value:
    evidence = (*left.evidence, *right.evidence)
    if (mode == "left" and left.status == ConfigurationStatus.KNOWN) or (
        mode == "right" and right.status == ConfigurationStatus.KNOWN
    ):
        return _known(cast("str | int", left.value if mode == "left" else right.value), evidence)
    uncertainties = (*left.uncertainties, *right.uncertainties)
    if uncertainties:
        return _unknown(uncertainties[0], evidence, uncertainties[1:])
    if ConfigurationStatus.UNSET in {left.status, right.status}:
        selected = right if left.status == ConfigurationStatus.UNSET else left
        return ConfigurationValue(selected.value, selected.status, evidence, ())
    if mode in {"left", "right"}:
        return _known(cast("str | int", left.value if mode == "left" else right.value), evidence)
    if type(left.value) is not int or type(right.value) is not int:
        return _unknown(ConfigurationUncertainty.INVALID_VALUE, evidence)
    maximum = mode == "max" or (mode == "limit" and (left.value < 0 or right.value < 0))
    return _known(max(left.value, right.value) if maximum else min(left.value, right.value), evidence)


def _default(setting: _Value, key: str, value: str | int) -> _Value:
    if setting.status != ConfigurationStatus.UNSET:
        return setting
    return _known(value, (ConfigurationEvidence(ConfigurationSource.BROKER_DEFAULT, key, value),))


def _derived(value: str | int, key: str, *inputs: _Value) -> _Value:
    evidence = tuple(item for setting in inputs for item in setting.evidence)
    return _known(value, (*evidence, ConfigurationEvidence(ConfigurationSource.DERIVED, key, value)))


def _positive_duration(setting: _Value) -> _Value:
    if type(setting.value) is int and setting.value <= 0:
        return _unknown(ConfigurationUncertainty.INVALID_VALUE, setting.evidence)
    return setting


@dataclass(frozen=True)
class _Resolver:
    definitions: ClusterDefinitionsResponse
    queue: ClusterExportQueue
    observed: QueueResponse | None
    selected_policy: str | None
    selection_captured: bool

    def _selected(self, key: str, *, operator: bool) -> _Value:
        if operator and key not in _OPERATOR_KEYS:
            return _state(ConfigurationStatus.UNSET)
        candidates = []
        if operator:
            for parameter in self.definitions.parameters:
                if parameter.component == "operator_policy" and parameter.vhost == self.queue.vhost:
                    policy = parameter.as_operator_policy()
                    if policy is not None and policy.apply_to in _SCOPES:
                        candidates.append((parameter.name, policy.definition.root))
        else:
            candidates = [
                (policy.name, policy.definition.root)
                for policy in self.definitions.policies
                if policy.vhost == self.queue.vhost and policy.apply_to in _SCOPES
            ]
        field = "operator_policy" if operator else "policy"
        captured = self.selection_captured if not operator else False
        name = self.selected_policy if not operator else None
        if self.observed is not None:
            captured = field in self.observed.model_fields_set
            name = getattr(self.observed, field)
        source = ConfigurationSource.OPERATOR_POLICY if operator else ConfigurationSource.REGULAR_POLICY
        if captured:
            if name is None:
                return _state(ConfigurationStatus.UNSET)
            matches = [values for policy_name, values in candidates if policy_name == name]
            if len(matches) != 1:
                if not operator and any(
                    policy.vhost == self.queue.vhost and policy.name == name and policy.apply_to not in _SCOPES
                    for policy in self.definitions.policies
                ):
                    raise TopologyParseError(f"Selected policy {name!r} has incompatible apply-to for a quorum queue")
                raise TopologyParseError(
                    f"Selected {field} {name!r} for queue {self.queue.vhost!r}/{self.queue.name!r} "
                    "does not identify exactly one compatible captured policy"
                )
            return _entry(matches[0], key, source, name)
        if not any(key in values for _, values in candidates):
            return _state(ConfigurationStatus.UNSET)
        return _unknown(
            ConfigurationUncertainty.OPERATOR_POLICY_SELECTION_NOT_CAPTURED
            if operator
            else ConfigurationUncertainty.POLICY_SELECTION_NOT_CAPTURED
        )

    def _policy(self, key: str) -> _Value:
        regular = self._selected(key, operator=False)
        operator = self._selected(key, operator=True)
        merged = _merge(regular, operator, _POLICY_MERGES.get(key, "left"))
        if self.observed is None or "effective_policy_definition" not in self.observed.model_fields_set:
            return merged
        effective = self.observed.effective_policy_definition
        if effective is None:
            return _unknown(ConfigurationUncertainty.CAPTURED_NULL, merged.evidence)
        captured = _entry(effective.root, key, ConfigurationSource.EFFECTIVE_POLICY)
        if merged.status != ConfigurationStatus.UNKNOWN and (
            captured.status != merged.status or captured.value != merged.value
        ):
            return _unknown(ConfigurationUncertainty.CONFLICTING_EVIDENCE, (*merged.evidence, *captured.evidence))
        return ConfigurationValue(
            captured.value, captured.status, (*merged.evidence, *captured.evidence), captured.uncertainties
        )

    def setting(self, key: str, mode: _Merge = "min") -> _Value:
        argument = _entry(self.queue.arguments.root, f"x-{key}", ConfigurationSource.QUEUE_ARGUMENT)
        return _merge(argument, self._policy(key), mode)


def resolve_quorum_delivery(
    definitions: ClusterDefinitionsResponse,
    queue: ClusterExportQueue,
    observed: QueueResponse | None,
    *,
    selected_policy: str | None,
    selection_captured: bool,
) -> QuorumDeliveryConfiguration:
    """Resolve only evidence supported by captured RabbitMQ 4.2/4.3 inputs."""
    resolver = _Resolver(definitions, queue, observed, selected_policy, selection_captured)
    version = definitions.rabbitmq_version or definitions.rabbit_version or definitions.product_version
    match = re.fullmatch(r"4\.([23])\.\d+(?:[-+].*)?", version or "")
    minor = int(match[1]) if match else None

    def supported(setting: _Value, *, only43: bool = False) -> _Value:
        if minor is None:
            return _unknown(
                ConfigurationUncertainty.UNSUPPORTED_BROKER_VERSION
                if version
                else ConfigurationUncertainty.BROKER_VERSION_NOT_CAPTURED,
                setting.evidence,
                setting.uncertainties,
            )
        if only43 and minor == _MINOR_42:
            return _state(ConfigurationStatus.NOT_APPLICABLE, setting.evidence)
        return setting

    def with_default(setting: _Value, key: str, value: str | int, *, only43: bool = False) -> _Value:
        if minor is None or (only43 and minor == _MINOR_42):
            return supported(setting, only43=only43)
        return supported(_default(setting, key, value), only43=only43)

    retry = with_default(resolver.setting("delayed-retry-type", "right"), "delayed-retry-type", "disabled", only43=True)
    minimum = supported(resolver.setting("delayed-retry-min"), only43=True)
    maximum = supported(resolver.setting("delayed-retry-max"), only43=True)
    effective_retry = retry
    if retry.value == "disabled":
        minimum = _state(ConfigurationStatus.NOT_APPLICABLE, minimum.evidence)
        maximum = _state(ConfigurationStatus.NOT_APPLICABLE, maximum.evidence)
    elif retry.status == ConfigurationStatus.KNOWN:
        if minimum.status == ConfigurationStatus.UNSET or (type(minimum.value) is int and minimum.value <= 0):
            effective_retry = _derived("disabled", "retry_without_positive_minimum", retry, minimum)
            minimum = _state(ConfigurationStatus.NOT_APPLICABLE, minimum.evidence)
            maximum = _state(ConfigurationStatus.NOT_APPLICABLE, maximum.evidence)
        elif minimum.status == ConfigurationStatus.UNKNOWN:
            effective_retry = _unknown(
                minimum.uncertainties[0], (*retry.evidence, *minimum.evidence), minimum.uncertainties[1:]
            )
        elif maximum.status == ConfigurationStatus.UNSET or (type(maximum.value) is int and maximum.value <= 0):
            maximum = _derived(cast("int", minimum.value), "retry_maximum_from_minimum", minimum, maximum)
    minimum = _positive_duration(minimum)
    maximum = _positive_duration(maximum)

    def timeout(key: str) -> _Value:
        setting = supported(resolver.setting(key), only43=True)
        if setting.status == ConfigurationStatus.UNSET:
            return _unknown(ConfigurationUncertainty.BROKER_DEFAULT_NOT_CAPTURED, setting.evidence)
        if type(setting.value) is int and setting.value < 0:
            return _unknown(ConfigurationUncertainty.INVALID_VALUE, setting.evidence)
        return setting

    exchange = supported(resolver.setting("dead-letter-exchange", "left"))
    strategy = with_default(resolver.setting("dead-letter-strategy", "left"), "dead-letter-strategy", "at-most-once")
    overflow = with_default(resolver.setting("overflow", "right"), "overflow", "drop-head")
    if overflow.value == "reject-publish-dlx":
        overflow = _derived("drop-head", "unsupported_quorum_overflow_fallback", overflow)
    effective_strategy = strategy
    if exchange.status == ConfigurationStatus.UNSET:
        effective_strategy = _derived("disabled", "no_dead_letter_exchange", exchange, strategy)
    elif exchange.status == ConfigurationStatus.UNKNOWN:
        effective_strategy = _unknown(
            exchange.uncertainties[0], (*strategy.evidence, *exchange.evidence), exchange.uncertainties[1:]
        )
    elif strategy.value == "at-least-once":
        if overflow.status == ConfigurationStatus.UNKNOWN:
            effective_strategy = _unknown(
                overflow.uncertainties[0], (*strategy.evidence, *overflow.evidence), overflow.uncertainties[1:]
            )
        elif overflow.value != "reject-publish":
            effective_strategy = _derived("at-most-once", "at_least_once_fallback", strategy, overflow)

    return QuorumDeliveryConfiguration(
        broker_version=version,
        configured_retry_type=cast("ConfigurationValue[str]", retry),
        delayed_retry_type=cast("ConfigurationValue[str]", effective_retry),
        delayed_retry_min=cast("ConfigurationValue[int]", minimum),
        delayed_retry_max=cast("ConfigurationValue[int]", maximum),
        delivery_limit=cast(
            "ConfigurationValue[int]",
            with_default(resolver.setting("delivery-limit", "limit"), "delivery-limit", 20),
        ),
        delivery_count_basis=cast(
            "ConfigurationValue[str]",
            supported(
                _known(
                    "failed" if minor == _MINOR_43 else "acquired",
                    (
                        ConfigurationEvidence(
                            ConfigurationSource.BROKER_DEFAULT,
                            "delivery_count_basis",
                            "failed" if minor == _MINOR_43 else "acquired",
                        ),
                    ),
                )
                if minor is not None
                else _state(ConfigurationStatus.UNSET)
            ),
        ),
        consumer_timeout=cast("ConfigurationValue[int]", timeout("consumer-timeout")),
        consumer_disconnected_timeout=cast("ConfigurationValue[int]", timeout("consumer-disconnected-timeout")),
        dead_letter_exchange=cast("ConfigurationValue[str]", exchange),
        dead_letter_strategy=cast("ConfigurationValue[str]", strategy),
        overflow=cast("ConfigurationValue[str]", overflow),
        effective_dead_letter_strategy=cast("ConfigurationValue[str]", effective_strategy),
    )
