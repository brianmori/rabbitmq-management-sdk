from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from rabbitmq_management_sdk.resources.v4.policies.schemas import (
    OperatorPolicyApplyTo,
    OperatorPolicyRequest,
    PolicyApplyTo,
    PolicyDefinition,
    PolicyRequest,
)

if TYPE_CHECKING:
    from rabbitmq_management_sdk.client.rabbitmq_client import RabbitMQClient


@pytest.mark.live
def test_create_get_delete_regular_policy(rabbitmq_client_compatibility: RabbitMQClient) -> None:
    name = "sdk-live-regular-policy"
    request = PolicyRequest(
        pattern="^sdk-live-regular-",
        definition=PolicyDefinition(max_length=1000),
        priority=1,
        apply_to=PolicyApplyTo.QUEUES,
    )

    rabbitmq_client_compatibility.policies.create(name, request)
    try:
        response = rabbitmq_client_compatibility.policies.get(name)
        assert response.definition.max_length == 1000
    finally:
        rabbitmq_client_compatibility.policies.delete(name)


@pytest.mark.live
def test_create_get_delete_operator_policy(rabbitmq_client_compatibility: RabbitMQClient) -> None:
    name = "sdk-live-operator-policy"
    request = OperatorPolicyRequest(
        pattern="^sdk-live-operator-",
        definition=PolicyDefinition(max_length=1000),
        priority=1,
        apply_to=OperatorPolicyApplyTo.QUEUES,
    )

    rabbitmq_client_compatibility.operator_policies.create(name, request)
    try:
        response = rabbitmq_client_compatibility.operator_policies.get(name)
        assert response.definition.max_length == 1000
    finally:
        rabbitmq_client_compatibility.operator_policies.delete(name)
