"""Tests for policy request and response schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from rabbitmq_management_sdk import (
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
    assert not isinstance(ExportPolicySettings(max_length=1000), PolicyDefinition)
    assert not issubclass(PolicyResponse, PolicyRequest)
