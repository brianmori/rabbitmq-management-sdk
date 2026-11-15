from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from rabbitmq_management_sdk import NotFoundError
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
def test_create_get_delete_regular_policy(live_client: RabbitMQClient) -> None:
    name = "sdk-live-regular-policy"
    request = PolicyRequest(
        pattern="^sdk-live-regular-",
        definition=PolicyDefinition(max_length=1000),
        priority=1,
        apply_to=PolicyApplyTo.QUEUES,
    )

    live_client.policies.create(name, request)
    try:
        response = live_client.policies.get(name)
        assert response.name == name
        assert response.pattern == request.pattern
        assert response.priority == request.priority
        assert response.apply_to == request.apply_to
        assert response.definition.max_length == 1000
    finally:
        live_client.policies.delete(name)
    with pytest.raises(NotFoundError):
        live_client.policies.get(name)


@pytest.mark.live
def test_create_get_delete_operator_policy(live_client: RabbitMQClient) -> None:
    name = "sdk-live-operator-policy"
    request = OperatorPolicyRequest(
        pattern="^sdk-live-operator-",
        definition=PolicyDefinition(max_length=1000),
        priority=1,
        apply_to=OperatorPolicyApplyTo.QUEUES,
    )

    live_client.operator_policies.create(name, request)
    try:
        response = live_client.operator_policies.get(name)
        assert response.name == name
        assert response.pattern == request.pattern
        assert response.priority == request.priority
        assert response.apply_to == request.apply_to
        assert response.definition.max_length == 1000
    finally:
        live_client.operator_policies.delete(name)
    with pytest.raises(NotFoundError):
        live_client.operator_policies.get(name)
