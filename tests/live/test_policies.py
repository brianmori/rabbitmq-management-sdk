from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import quote

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
    from tests.shared.live_broker import ManagementAPI

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
def test_legacy_leader_locators_round_trip_from_broker(
    live_client: RabbitMQClient, live_vhost: str, management_api: ManagementAPI
) -> None:
    vhost_path = quote(live_vhost, safe="")
    policy_locators = ("random", "least-leaders")
    queue_locators = (*policy_locators, "min-masters")
    for locator in policy_locators:
        management_api.request(
            "PUT",
            f"policies/{vhost_path}/{locator}",
            {
                "pattern": f"^{locator}$",
                "definition": {"queue-leader-locator": locator},
                "priority": 0,
                "apply-to": "quorum_queues",
            },
        )
        assert live_client.policies.get(locator).definition.queue_leader_locator == locator

    for locator in queue_locators:
        management_api.request(
            "PUT",
            f"queues/{vhost_path}/{locator}",
            {
                "durable": True,
                "auto_delete": False,
                "arguments": {"x-queue-type": "quorum", "x-queue-leader-locator": locator},
            },
        )

    definitions = live_client.admin.export_definitions()
    assert {
        policy.name: policy.definition.queue_leader_locator
        for policy in definitions.policies
        if policy.vhost == live_vhost
    } == {locator: locator for locator in policy_locators}
    assert {
        queue.name: queue.arguments.queue_leader_locator for queue in definitions.queues if queue.vhost == live_vhost
    } == {locator: locator for locator in queue_locators}


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
