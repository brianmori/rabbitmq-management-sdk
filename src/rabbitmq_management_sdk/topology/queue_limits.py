"""Resolve queue limits and stream retention from captured configuration."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from rabbitmq_management_sdk.topology.models import (
    ConfigurationSource,
    ConfigurationStatus,
    ConfigurationUncertainty,
    ConfigurationValue,
    QueueLimitsConfiguration,
)
from rabbitmq_management_sdk.topology.queue_configuration import (
    _default,
    _derived,
    _entry,
    _unknown,
    captured_broker_version,
    unknown_version,
)

if TYPE_CHECKING:
    from typing import Never

    from rabbitmq_management_sdk.topology.dead_letter import DeadLetterCoverage
    from rabbitmq_management_sdk.topology.models import NodeId, QuorumDeliveryConfiguration
    from rabbitmq_management_sdk.topology.queue_configuration import _Resolver, _Value

_AGE_UNIT_MS = {
    "Y": 365 * 86_400_000,
    "M": 30 * 86_400_000,
    "D": 86_400_000,
    "h": 3_600_000,
    "m": 60_000,
    "s": 1_000,
}
_NOT_APPLICABLE: ConfigurationValue[Never] = ConfigurationValue(
    status=ConfigurationStatus.NOT_APPLICABLE, uncertainties=()
)
_STANDARD_TYPES = frozenset({"classic", "quorum", "stream"})


def _stream_age_ms(setting: _Value) -> ConfigurationValue[int]:
    if setting.value is None:
        return cast("ConfigurationValue[int]", setting)
    match = re.fullmatch(r"([0-9]+)([YMDhms])", setting.value) if isinstance(setting.value, str) else None
    try:
        amount = int(match[1]) if match else 0
    except ValueError:
        amount = 0
    if match is None or amount == 0:
        return cast("ConfigurationValue[int]", _unknown(ConfigurationUncertainty.INVALID_VALUE, setting.evidence))
    age = amount * _AGE_UNIT_MS[match[2]]
    return cast("ConfigurationValue[int]", _derived(age, "stream_max_age_ms", setting))


def resolve_queue_limits(
    resolver: _Resolver, quorum_delivery: QuorumDeliveryConfiguration | None
) -> QueueLimitsConfiguration:
    """Resolve versioned per-type settings while constructing a queue node."""
    version, minor = captured_broker_version(resolver.definitions)
    overflow: ConfigurationValue[str]
    stream_age: ConfigurationValue[int]

    def unsupported_type(key: str) -> _Value:
        argument = _entry(resolver.queue.arguments.root, f"x-{key}", ConfigurationSource.QUEUE_ARGUMENT)
        return _unknown(ConfigurationUncertainty.UNSUPPORTED_QUEUE_TYPE, argument.evidence, argument.uncertainties)

    def number(key: str, *, minimum: int = 0, stream: bool = False) -> ConfigurationValue[int]:
        if resolver.queue_type not in _STANDARD_TYPES:
            return cast("ConfigurationValue[int]", unsupported_type(key))
        if stream != (resolver.queue_type == "stream"):
            return _NOT_APPLICABLE
        setting = resolver.setting(key, "right" if stream else "min", minimum=minimum)
        return cast("ConfigurationValue[int]", setting if minor is not None else unknown_version(setting, version))

    if resolver.queue_type == "stream":
        age = resolver.setting("max-age", "right")
        stream_age = _stream_age_ms(age if minor is not None else unknown_version(age, version))
        overflow = _NOT_APPLICABLE
    elif resolver.queue_type in _STANDARD_TYPES:
        stream_age = _NOT_APPLICABLE
        if quorum_delivery is not None:
            overflow = quorum_delivery.overflow
        else:
            setting = resolver.setting("overflow", "left")
            overflow = cast(
                "ConfigurationValue[str]",
                _default(setting, "overflow", "drop-head") if minor is not None else unknown_version(setting, version),
            )
    else:
        stream_age = cast("ConfigurationValue[int]", unsupported_type("max-age"))
        overflow = cast("ConfigurationValue[str]", unsupported_type("overflow"))

    return QueueLimitsConfiguration(
        broker_version=version,
        max_length=number("max-length"),
        max_length_bytes=number("max-length-bytes"),
        overflow=overflow,
        message_ttl_ms=number("message-ttl"),
        queue_expiry_ms=number("expires", minimum=1),
        stream_max_age_ms=stream_age,
        stream_max_length_bytes=number("max-length-bytes", stream=True),
    )


@dataclass(frozen=True, slots=True)
class QueueLimits:
    """Limits and existing dead-letter coverage for one declared queue."""

    configuration: QueueLimitsConfiguration
    dead_letter_coverage: DeadLetterCoverage

    @property
    def queue(self) -> NodeId:
        """Return the full identity of the declared queue."""
        return self.dead_letter_coverage.queue.id

    @property
    def queue_type(self) -> str:
        """Return the queue type represented by the captured node."""
        return self.dead_letter_coverage.queue.queue_type


def queue_limits(coverage: tuple[DeadLetterCoverage, ...]) -> tuple[QueueLimits, ...]:
    """Pair retained limits with the same canonically ordered DLX results."""
    results = []
    for dlx in coverage:
        queue = dlx.queue
        limits = queue.queue_limits
        if limits is None:
            if queue.queue_type == "stream":
                limits = QueueLimitsConfiguration(
                    max_length=_NOT_APPLICABLE,
                    max_length_bytes=_NOT_APPLICABLE,
                    overflow=_NOT_APPLICABLE,
                    message_ttl_ms=_NOT_APPLICABLE,
                    queue_expiry_ms=_NOT_APPLICABLE,
                )
            elif queue.queue_type in {"classic", "quorum"}:
                delivery = queue.quorum_delivery
                limits = QueueLimitsConfiguration(
                    broker_version=delivery.broker_version if delivery is not None else None,
                    overflow=delivery.overflow if delivery is not None else ConfigurationValue(),
                    stream_max_age_ms=_NOT_APPLICABLE,
                    stream_max_length_bytes=_NOT_APPLICABLE,
                )
            else:
                unknown: ConfigurationValue[Never] = ConfigurationValue(
                    uncertainties=(ConfigurationUncertainty.UNSUPPORTED_QUEUE_TYPE,)
                )
                limits = QueueLimitsConfiguration(None, unknown, unknown, unknown, unknown, unknown, unknown, unknown)
        results.append(QueueLimits(limits, dlx))
    return tuple(results)
