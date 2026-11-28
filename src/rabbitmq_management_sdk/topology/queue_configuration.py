"""Resolve captured queue settings from declarations and broker-selected policies."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cached_property
from typing import TYPE_CHECKING, Literal, cast

from pydantic import ValidationError

from rabbitmq_management_sdk.exceptions import TopologyParseError
from rabbitmq_management_sdk.resources.v4.common import DeadLetterStrategy, OverflowBehaviour
from rabbitmq_management_sdk.resources.v4.queues.schemas.queue_response import QuorumQueueResponse, StreamQueueResponse
from rabbitmq_management_sdk.topology.models import (
    ConfigurationEvidence,
    ConfigurationSource,
    ConfigurationStatus,
    ConfigurationUncertainty,
    ConfigurationValue,
    NodeId,
    NodeKind,
)
from rabbitmq_management_sdk.topology.policy_routes import _apply_to_matches, _observed_user_policy

if TYPE_CHECKING:
    from collections.abc import Mapping

    from rabbitmq_management_sdk.resources.v4.admin.schemas.export_response import (
        ClusterDefinitionsResponse,
        ClusterExportQueue,
    )
    from rabbitmq_management_sdk.resources.v4.queues.schemas.queue_response import QueueResponse

type _Value = ConfigurationValue[str | int]
type _Merge = Literal["left", "right", "min", "max", "limit"]
type _Policy = tuple[str, Mapping[str, object]]

_POLICY_MERGES: dict[str, _Merge] = {
    "delayed-retry-type": "right",
    "delayed-retry-min": "max",
    "delayed-retry-max": "min",
    "delivery-limit": "limit",
    "overflow": "right",
    "max-length": "min",
    "max-length-bytes": "min",
    "message-ttl": "min",
    "expires": "min",
}
_MAX_DECLARED_EXPIRY_MS = 315_360_000_000
_OBSERVED_QUORUM_ARGUMENT_KEYS = frozenset(field.alias for field in QuorumQueueResponse.model_fields.values())
_OBSERVED_QUORUM_ARGUMENT_CHOICES = {
    "x-dead-letter-strategy": frozenset(DeadLetterStrategy),
    "x-overflow": frozenset(OverflowBehaviour),
}


def captured_broker_version(definitions: ClusterDefinitionsResponse) -> tuple[str | None, int | None]:
    """Return the captured version and a supported 4.2/4.3 minor number."""
    version = definitions.rabbitmq_version or definitions.rabbit_version or definitions.product_version
    match = re.fullmatch(r"4\.([23])\.\d+(?:[-+].*)?", version or "")
    return version, int(match[1]) if match else None


def unknown_version(setting: _Value, version: str | None) -> _Value:
    """Retain observed inputs when the capture cannot select a versioned rule."""
    reason = (
        ConfigurationUncertainty.UNSUPPORTED_BROKER_VERSION
        if version
        else ConfigurationUncertainty.BROKER_VERSION_NOT_CAPTURED
    )
    return _unknown(reason, setting.evidence, setting.uncertainties)


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


def _entry(
    values: Mapping[str, object],
    key: str,
    source: ConfigurationSource,
    name: str | None = None,
    *,
    minimum: int | None = None,
) -> _Value:
    if key not in values:
        return _state(ConfigurationStatus.UNSET)
    value = values[key]
    if value is not None and type(value) not in (str, int):
        return _unknown(ConfigurationUncertainty.INVALID_VALUE)
    scalar = cast("str | int | None", value)
    evidence = (ConfigurationEvidence(source, key, scalar, name),)
    if scalar is None:
        return _unknown(ConfigurationUncertainty.CAPTURED_NULL, evidence)
    if minimum is not None and (type(scalar) is not int or scalar < minimum):
        return _unknown(ConfigurationUncertainty.INVALID_VALUE, evidence)
    if key in {"x-message-ttl", "x-expires"} and type(scalar) is int and scalar > _MAX_DECLARED_EXPIRY_MS:
        return _unknown(ConfigurationUncertainty.INVALID_VALUE, evidence)
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


@dataclass(frozen=True)
class _Resolver:
    definitions: ClusterDefinitionsResponse
    queue: ClusterExportQueue
    observed: QueueResponse | None
    selected_policy: str | None
    selection_captured: bool
    queue_type: str

    @cached_property
    def _regular(self) -> tuple[_Policy, ...]:
        return tuple(
            (policy.name, policy.definition.root)
            for policy in self.definitions.policies
            if policy.vhost == self.queue.vhost and _apply_to_matches(policy.apply_to, NodeKind.QUEUE, self.queue_type)
        )

    @cached_property
    def _operator(self) -> tuple[tuple[_Policy, ...], frozenset[str]]:
        candidates: list[_Policy] = []
        malformed: set[str] = set()
        for parameter in self.definitions.parameters:
            if parameter.component == "operator_policy" and parameter.vhost == self.queue.vhost:
                try:
                    policy = parameter.as_operator_policy()
                except ValidationError:
                    malformed.add(parameter.name)
                    continue
                if policy is not None and _apply_to_matches(policy.apply_to, NodeKind.QUEUE, self.queue_type):
                    candidates.append((parameter.name, policy.definition.root))
        return tuple(candidates), frozenset(malformed)

    def _selected(self, key: str, *, operator: bool, minimum: int | None) -> _Value:
        if operator and key not in _POLICY_MERGES:
            return _state(ConfigurationStatus.UNSET)
        field = "operator_policy" if operator else "policy"
        captured = self.selection_captured if not operator else False
        name = self.selected_policy if not operator else None
        if self.observed is not None:
            captured = field in self.observed.model_fields_set
            name = getattr(self.observed, field)
        source = ConfigurationSource.OPERATOR_POLICY if operator else ConfigurationSource.REGULAR_POLICY
        if captured and name is None:
            return _state(ConfigurationStatus.UNSET)
        section = "parameters" if operator else "policies"
        if section not in self.definitions.model_fields_set:
            return _unknown(
                ConfigurationUncertainty.OPERATOR_POLICY_DEFINITIONS_NOT_CAPTURED
                if operator
                else ConfigurationUncertainty.POLICY_DEFINITIONS_NOT_CAPTURED
            )
        candidates, malformed = self._operator if operator else (self._regular, frozenset())
        if captured:
            matches = [values for policy_name, values in candidates if policy_name == name]
            if len(matches) != 1 or name in malformed:
                if not operator:
                    queue_id = NodeId(self.queue.vhost, self.queue.name, NodeKind.QUEUE)
                    _observed_user_policy(
                        node_id=queue_id,
                        queue_type=self.queue_type,
                        policies=self.definitions.policies,
                        user_policy_selections={queue_id: name},
                    )
                raise TopologyParseError(
                    f"Selected {field} {name!r} for queue {self.queue.vhost!r}/{self.queue.name!r} "
                    "does not identify exactly one compatible captured policy"
                )
            return _entry(matches[0], key, source, name, minimum=minimum)
        if not malformed and not any(key in values for _, values in candidates):
            return _state(ConfigurationStatus.UNSET)
        reason = (
            ConfigurationUncertainty.OPERATOR_POLICY_SELECTION_NOT_CAPTURED
            if operator
            else ConfigurationUncertainty.POLICY_SELECTION_NOT_CAPTURED
        )
        return _unknown(ConfigurationUncertainty.INVALID_VALUE if malformed else reason)

    def _policy(self, key: str, *, minimum: int | None) -> _Value:
        regular = self._selected(key, operator=False, minimum=minimum)
        operator = self._selected(key, operator=True, minimum=minimum)
        merged = _merge(regular, operator, _POLICY_MERGES.get(key, "left"))
        if self.observed is None or "effective_policy_definition" not in self.observed.model_fields_set:
            return merged
        effective = self.observed.effective_policy_definition
        if effective is None:
            return _unknown(ConfigurationUncertainty.CAPTURED_NULL, merged.evidence)
        captured = _entry(effective.root, key, ConfigurationSource.EFFECTIVE_POLICY, minimum=minimum)
        if (
            minimum is not None
            and captured.status != ConfigurationStatus.UNKNOWN
            and any(
                type(item.value) is int and (type(captured.value) is not int or captured.value > item.value)
                for item in (regular, operator)
            )
        ):
            return _unknown(ConfigurationUncertainty.CONFLICTING_EVIDENCE, (*merged.evidence, *captured.evidence))
        if merged.status != ConfigurationStatus.UNKNOWN and (
            captured.status != merged.status or captured.value != merged.value
        ):
            return _unknown(ConfigurationUncertainty.CONFLICTING_EVIDENCE, (*merged.evidence, *captured.evidence))
        return ConfigurationValue(
            captured.value, captured.status, (*merged.evidence, *captured.evidence), captured.uncertainties
        )

    @cached_property
    def _observed_quorum_arguments(self) -> Mapping[str, object] | None:
        if self.observed is not None and isinstance(self.observed.arguments, QuorumQueueResponse):
            return self.observed.arguments.model_dump(by_alias=True, exclude_unset=True)
        return None

    def _argument(self, key: str, *, minimum: int | None) -> _Value:
        if "arguments" in self.queue.model_fields_set:
            return _entry(self.queue.arguments.root, key, ConfigurationSource.QUEUE_ARGUMENT, minimum=minimum)
        if key == "x-stream-max-segment-size-bytes" and self.observed is not None:
            arguments = self.observed.arguments
            if isinstance(arguments, StreamQueueResponse):
                observed_stream = arguments.model_dump(by_alias=True, exclude_unset=True)
                return _entry(observed_stream, key, ConfigurationSource.OBSERVED_QUEUE_ARGUMENT, minimum=minimum)
        observed = self._observed_quorum_arguments
        if observed is None or key not in _OBSERVED_QUORUM_ARGUMENT_KEYS:
            return _unknown(ConfigurationUncertainty.ARGUMENTS_NOT_CAPTURED)
        setting = _entry(observed, key, ConfigurationSource.OBSERVED_QUEUE_ARGUMENT, minimum=minimum)
        choices = _OBSERVED_QUORUM_ARGUMENT_CHOICES.get(key)
        if choices is not None and setting.status == ConfigurationStatus.KNOWN and setting.value not in choices:
            return _unknown(ConfigurationUncertainty.INVALID_VALUE, setting.evidence)
        return setting

    def setting(self, key: str, mode: _Merge = "min", *, minimum: int | None = None) -> _Value:
        return _merge(self._argument(f"x-{key}", minimum=minimum), self._policy(key, minimum=minimum), mode)
