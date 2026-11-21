"""Tests for policy request and response schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from rabbitmq_management_sdk import (
    DelayedRetryType,
    OperatorPolicyApplyTo,
    OperatorPolicyRequest,
    PolicyApplyTo,
    PolicyDefinition,
    PolicyDefinitionResponse,
    PolicyRequest,
    PolicyResponse,
)
from rabbitmq_management_sdk.resources.v4.admin.schemas.export_response import ExportPolicySettings


@pytest.mark.unit
def test_policy_request_serializes_wire_aliases_and_plugin_keys() -> None:
    request = PolicyRequest(
        pattern="^orders\\.",
        definition=PolicyDefinition.model_validate(
            {
                "dead-letter-exchange": "orders.dlx",
                "federation-upstream-set": "all",
            }
        ),
        priority=10,
        apply_to=PolicyApplyTo.QUEUES,
    )

    assert request.model_dump(by_alias=True, exclude_none=True) == {
        "pattern": "^orders\\.",
        "definition": {
            "dead-letter-exchange": "orders.dlx",
            "federation-upstream-set": "all",
        },
        "priority": 10,
        "apply-to": "queues",
    }


@pytest.mark.unit
def test_policy_request_requires_all_rabbitmq_payload_fields() -> None:
    with pytest.raises(ValidationError):
        PolicyRequest.model_validate(
            {
                "pattern": ".*",
                "definition": {"max-length": 1000},
                "apply-to": "queues",
            }
        )


@pytest.mark.unit
def test_operator_policy_rejects_exchange_scope() -> None:
    with pytest.raises(ValidationError):
        OperatorPolicyRequest.model_validate(
            {
                "pattern": ".*",
                "definition": {"max-length": 1000},
                "priority": 0,
                "apply-to": "exchanges",
            }
        )

    request = OperatorPolicyRequest(
        pattern=".*",
        definition=PolicyDefinition(max_length=1000),
        priority=0,
        apply_to=OperatorPolicyApplyTo.QUEUES,
    )
    assert request.apply_to == "queues"


@pytest.mark.unit
def test_policy_models_are_frozen() -> None:
    request = PolicyRequest(
        pattern=".*",
        definition=PolicyDefinition(max_length=1000),
        priority=0,
        apply_to=PolicyApplyTo.ALL,
    )

    with pytest.raises(ValidationError):
        request.priority = 1  # type: ignore[misc]


@pytest.mark.unit
def test_policy_api_and_definitions_export_use_independent_models() -> None:
    response = PolicyResponse.model_validate(
        {
            "vhost": "/",
            "name": "limits",
            "pattern": ".*",
            "definition": {"max-length": 1000},
            "priority": 0,
            "apply-to": "queues",
        }
    )

    assert isinstance(response.definition, PolicyDefinitionResponse)
    assert not isinstance(response.definition, PolicyDefinition)
    assert not isinstance(ExportPolicySettings.from_fields(max_length=1000), PolicyDefinition)
    assert not issubclass(PolicyResponse, PolicyRequest)


@pytest.mark.unit
@pytest.mark.parametrize("retry_type", list(DelayedRetryType))
def test_policy_quorum_configuration_names_and_aliases(retry_type: DelayedRetryType) -> None:
    definition = PolicyDefinition(
        delayed_retry_type=retry_type,
        delayed_retry_min=1000,
        delayed_retry_max=30_000,
        consumer_timeout=0,
        consumer_disconnected_timeout=60_000,
    )
    wire = definition.model_dump(by_alias=True, exclude_none=True)
    assert wire == {
        "delayed-retry-type": retry_type.value,
        "delayed-retry-min": 1000,
        "delayed-retry-max": 30_000,
        "consumer-timeout": 0,
        "consumer-disconnected-timeout": 60_000,
    }
    assert PolicyDefinition.model_validate(wire) == definition


@pytest.mark.unit
@pytest.mark.parametrize(
    "values",
    [
        {"delayed-retry-type": "all"},
        {"delayed-retry-type": "failed"},
        {"delayed-retry-type": "returned"},
        {"delayed-retry-type": "invalid"},
        {"delayed-retry-min": 1000, "delayed-retry-max": 999},
        {"delayed-retry-type": "disabled", "delayed-retry-min": 1000, "delayed-retry-max": 999},
    ],
)
def test_policy_retry_constraints_match_broker_validation(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PolicyDefinition.model_validate(values)


@pytest.mark.unit
@pytest.mark.parametrize("field", ["delayed-retry-min", "delayed-retry-max"])
@pytest.mark.parametrize("value", [0, -1, 1.5, True, "1000", "1s"])
def test_policy_retry_delays_require_positive_integer_milliseconds(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        PolicyDefinition.model_validate({field: value})


@pytest.mark.unit
@pytest.mark.parametrize("field", ["consumer-timeout", "consumer-disconnected-timeout"])
@pytest.mark.parametrize("value", [-1, 1.5, True, "1000", "1s"])
def test_policy_timeouts_require_nonnegative_integer_milliseconds(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        PolicyDefinition.model_validate({field: value})


@pytest.mark.unit
@pytest.mark.parametrize(
    "values",
    [
        {},
        {"delayed-retry-type": "disabled"},
        {"delayed-retry-min": 1},
        {"delayed-retry-max": 1},
        {"delayed-retry-type": "all", "delayed-retry-min": 1},
        {"delayed-retry-min": 1, "delayed-retry-max": 1},
        {"consumer-timeout": 0, "consumer-disconnected-timeout": 0},
    ],
)
def test_policy_accepts_independent_bounds_disabled_retry_and_zero_timeouts(values: dict[str, object]) -> None:
    assert PolicyDefinition.model_validate(values).model_dump(by_alias=True, exclude_none=True) == values
