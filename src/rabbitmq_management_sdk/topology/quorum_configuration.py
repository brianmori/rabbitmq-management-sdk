"""Resolve captured quorum delivery configuration without policy regex matching."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from rabbitmq_management_sdk.topology.models import (
    ConfigurationEvidence,
    ConfigurationSource,
    ConfigurationStatus,
    ConfigurationUncertainty,
    QuorumDeliveryConfiguration,
)
from rabbitmq_management_sdk.topology.queue_configuration import (
    _default,
    _derived,
    _known,
    _state,
    _unknown,
    _Value,
    captured_broker_version,
    unknown_version,
)

if TYPE_CHECKING:
    from rabbitmq_management_sdk.topology.models import ConfigurationValue
    from rabbitmq_management_sdk.topology.queue_configuration import _Resolver

_MINOR_42 = 2
_MINOR_43 = 3


def _positive_duration(setting: _Value) -> _Value:
    if type(setting.value) is int and setting.value <= 0:
        return _unknown(ConfigurationUncertainty.INVALID_VALUE, setting.evidence)
    return setting


def resolve_quorum_delivery(resolver: _Resolver) -> QuorumDeliveryConfiguration:
    """Resolve only evidence supported by captured RabbitMQ 4.2/4.3 inputs."""
    version, minor = captured_broker_version(resolver.definitions)

    def supported(setting: _Value, *, only43: bool = False) -> _Value:
        if minor is None:
            return unknown_version(setting, version)
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
