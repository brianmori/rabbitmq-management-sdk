"""Tests for queue request values shared with other resource domains."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from rabbitmq_management_sdk import (
    ClassicQueueRequest,
    DelayedRetryType,
    OverflowBehaviour,
    PolicyDefinition,
    QuorumQueueOverflow,
    QuorumQueueRequest,
    StreamQueueRequest,
)


@pytest.mark.unit
@pytest.mark.parametrize("age", ["1Y", "1M", "7D", "01h", "30m", "1s"])
def test_stream_max_age_accepts_broker_units(age: str) -> None:
    assert StreamQueueRequest(max_age=age).model_dump(by_alias=True)["x-max-age"] == age
    assert PolicyDefinition(max_age=age).model_dump(by_alias=True)["max-age"] == age


@pytest.mark.unit
@pytest.mark.parametrize("age", ["7d", "0D", "7D\n", "\uff17D", "1D2h"])
def test_stream_max_age_rejects_invalid_durations(age: str) -> None:
    with pytest.raises(ValidationError):
        StreamQueueRequest.model_validate({"x-max-age": age})
    with pytest.raises(ValidationError):
        PolicyDefinition.model_validate({"max-age": age})


@pytest.mark.unit
def test_classic_queue_accepts_shared_reject_publish_dlx_behaviour() -> None:
    request = ClassicQueueRequest(overflow=OverflowBehaviour.REJECT_PUBLISH_DLX)

    assert request.model_dump(by_alias=True, exclude_none=True)["x-overflow"] == "reject-publish-dlx"


@pytest.mark.unit
def test_quorum_queue_keeps_its_narrower_overflow_domain() -> None:
    request = QuorumQueueRequest(overflow=QuorumQueueOverflow.REJECT_PUBLISH)
    assert request.overflow == "reject-publish"

    with pytest.raises(ValidationError):
        QuorumQueueRequest.model_validate({"x-overflow": "reject-publish-dlx"})


@pytest.mark.unit
@pytest.mark.parametrize("retry_type", list(DelayedRetryType))
def test_quorum_configuration_accepts_python_names_and_wire_aliases(retry_type: DelayedRetryType) -> None:
    request = QuorumQueueRequest(
        delayed_retry_type=retry_type,
        delayed_retry_min=1000,
        delayed_retry_max=30_000,
        consumer_timeout=0,
        consumer_disconnected_timeout=60_000,
    )
    wire = request.model_dump(by_alias=True, exclude_none=True)
    assert wire == {
        "x-queue-type": "quorum",
        "x-delivery-limit": 20,
        "x-overflow": "drop-head",
        "x-delayed-retry-type": retry_type.value,
        "x-delayed-retry-min": 1000,
        "x-delayed-retry-max": 30_000,
        "x-consumer-timeout": 0,
        "x-consumer-disconnected-timeout": 60_000,
    }
    assert QuorumQueueRequest.model_validate(wire) == request


@pytest.mark.unit
@pytest.mark.parametrize("field", ["delayed_retry_min", "delayed_retry_max"])
@pytest.mark.parametrize("value", [0, -1, 1.5, True, "1000", "1s"])
def test_quorum_retry_delays_require_positive_integer_milliseconds(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        QuorumQueueRequest.model_validate({field: value})


@pytest.mark.unit
@pytest.mark.parametrize("field", ["consumer_timeout", "consumer_disconnected_timeout"])
@pytest.mark.parametrize("value", [-1, 1.5, True, "1000", "1s"])
def test_quorum_timeouts_require_nonnegative_integer_milliseconds(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        QuorumQueueRequest.model_validate({field: value})


@pytest.mark.unit
def test_queue_arguments_do_not_enforce_policy_cross_field_constraints() -> None:
    # Broker resolves queue arguments and policy together, independently per key.
    assert QuorumQueueRequest(delayed_retry_type=DelayedRetryType.ALL).delayed_retry_min is None
    request = QuorumQueueRequest(delayed_retry_min=1000, delayed_retry_max=1)
    assert request.delayed_retry_max == 1
    assert QuorumQueueRequest(consumer_disconnected_timeout=0).consumer_disconnected_timeout == 0
    with pytest.raises(ValidationError):
        QuorumQueueRequest.model_validate({"x-delayed-retry-type": "exponential"})
