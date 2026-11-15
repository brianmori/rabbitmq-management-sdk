"""Verify DLX audit evidence against disposable broker routing."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from tests.shared.live_broker import wait_for

from rabbitmq_management_sdk import BindingRequest, ClusterAuditor, ExchangeRequest
from rabbitmq_management_sdk.resources.v4.exchanges.schemas.exchange_request import ExchangeArguments
from rabbitmq_management_sdk.resources.v4.policies.schemas import PolicyApplyTo, PolicyDefinition, PolicyRequest
from rabbitmq_management_sdk.resources.v4.queues.schemas.queue_request import ClassicQueueRequest, QueueRequest
from rabbitmq_management_sdk.topology import DeadLetterCoverageStatus

if TYPE_CHECKING:
    from tests.shared.live_broker import ManagementAPI

    from rabbitmq_management_sdk import RabbitMQClient
    from rabbitmq_management_sdk.topology import DeadLetterCoverage

pytestmark = pytest.mark.live


def _coverage(client: RabbitMQClient) -> DeadLetterCoverage:
    auditor = ClusterAuditor(
        client.admin.export_definitions(),
        queues=client.queues.list_all(disable_stats=True),
        exchanges=client.exchanges.list_all(disable_stats=True),
    )
    return next(result for result in auditor.dead_letter_coverage() if result.queue.id.name == "work")


@pytest.mark.parametrize(
    "exchange_type, key, binding, expected, routed",
    [
        ("direct", "failed", "failed", "destination_possible", True),
        ("direct", "failed", "success", "no_destination", False),
        ("direct", "", "", "destination_possible", True),
        ("topic", "orders.failed", "orders.*", "destination_possible", True),
        ("topic", "orders.failed.extra", "orders.*", "no_destination", False),
        ("topic", "", "#", "destination_possible", True),
        ("topic", "", "*", "no_destination", False),
        ("topic", ".", "*.*", "destination_possible", True),
        ("fanout", None, "ignored", "destination_possible", True),
        ("direct", None, "work", "undetermined", True),
    ],
)
def test_known_and_original_keys_match_broker_routing(
    live_client: RabbitMQClient,
    live_vhost: str,
    management_api: ManagementAPI,
    exchange_type: str,
    key: str | None,
    binding: str,
    expected: str,
    routed: bool,
) -> None:
    client = live_client
    client.exchanges.create("dlx", ExchangeRequest(type=exchange_type))
    client.queues.create("parking", QueueRequest(arguments=ClassicQueueRequest()))
    client.bindings.create_exchange_to_queue("dlx", "parking", BindingRequest(routing_key=binding))
    client.queues.create(
        "work", QueueRequest(arguments=ClassicQueueRequest(dead_letter_exchange="dlx", dead_letter_routing_key=key))
    )

    result = _coverage(client)
    assert result.status == expected
    assert management_api.publish(live_vhost, "dlx", key if key is not None else "work", "routing-probe") is routed
    if routed:
        management_api.delivered(live_vhost, "parking", "routing-probe")
    assert management_api.publish(live_vhost, "amq.default", "work", "rejected-message")
    wait_for(lambda: management_api.messages(live_vhost, "work", ackmode="reject_requeue_false"), "work rejection")
    if routed:
        management_api.delivered(live_vhost, "parking", "rejected-message")


@pytest.mark.parametrize(
    "mode, header_key, expected, routed",
    [
        ("all", None, "destination_possible", True),
        ("any", None, "no_destination", False),
        ("all", "department", "undetermined", True),
        ("all", "x-custom", "destination_possible", True),
        ("any", "x-custom", "no_destination", False),
        ("all-with-x", "x-custom", "undetermined", True),
        ("any-with-x", "x-custom", "undetermined", True),
    ],
)
def test_header_uncertainties_and_unconditional_bindings(
    live_client: RabbitMQClient,
    live_vhost: str,
    management_api: ManagementAPI,
    mode: str,
    header_key: str | None,
    expected: str,
    routed: bool,
) -> None:
    client = live_client
    client.exchanges.create("dlx", ExchangeRequest(type="headers"))
    client.queues.create("parking", QueueRequest(arguments=ClassicQueueRequest()))
    arguments = {"x-match": mode}
    if header_key:
        arguments[header_key] = "sales"
    client.bindings.create_exchange_to_queue("dlx", "parking", BindingRequest(arguments=arguments))
    client.queues.create("work", QueueRequest(arguments=ClassicQueueRequest(dead_letter_exchange="dlx")))

    assert _coverage(client).status == expected
    headers = {header_key: "sales"} if header_key else {}
    assert management_api.publish(live_vhost, "dlx", "unused", "header-probe", headers=headers) is routed
    if routed:
        management_api.delivered(live_vhost, "parking", "header-probe")
    assert management_api.publish(live_vhost, "amq.default", "work", "header-rejection", headers=headers)
    wait_for(lambda: management_api.messages(live_vhost, "work", ackmode="reject_requeue_false"), "header rejection")
    if routed:
        management_api.delivered(live_vhost, "parking", "header-rejection")


def test_matching_exchange_binding_suppresses_parent_alternate(
    live_client: RabbitMQClient, live_vhost: str, management_api: ManagementAPI
) -> None:
    client = live_client
    client.exchanges.create("fallback", ExchangeRequest(type="fanout"))
    client.exchanges.create("dead-end", ExchangeRequest(type="direct"))
    client.exchanges.create(
        "dlx", ExchangeRequest(type="direct", arguments=ExchangeArguments(alternate_exchange="fallback"))
    )
    client.queues.create("parking", QueueRequest(arguments=ClassicQueueRequest()))
    client.bindings.create_exchange_to_queue("fallback", "parking", BindingRequest())
    binding = client.bindings.create_exchange_to_exchange("dlx", "dead-end", BindingRequest(routing_key="failed"))
    client.queues.create(
        "work",
        QueueRequest(arguments=ClassicQueueRequest(dead_letter_exchange="dlx", dead_letter_routing_key="failed")),
    )

    assert _coverage(client).status == DeadLetterCoverageStatus.NO_DESTINATION
    assert not management_api.publish(live_vhost, "dlx", "failed", "suppressed-fallback")
    client.bindings.delete_exchange_to_exchange("dlx", "dead-end", binding.properties_key)
    assert _coverage(client).status == DeadLetterCoverageStatus.DESTINATION_POSSIBLE
    assert management_api.publish(live_vhost, "dlx", "failed", "active-fallback")
    management_api.delivered(live_vhost, "parking", "active-fallback")


@pytest.mark.parametrize("override", [False, True])
def test_broker_selected_policy_and_argument_precedence(live_client: RabbitMQClient, override: bool) -> None:
    client = live_client
    for name in ("policy-dlx", "argument-dlx"):
        client.exchanges.create(name, ExchangeRequest(type="direct"))
        client.queues.create(name, QueueRequest(arguments=ClassicQueueRequest()))
        client.bindings.create_exchange_to_queue(name, name, BindingRequest(routing_key="policy-key"))
    client.policies.create(
        "dlx-policy",
        PolicyRequest(
            pattern="^work$",
            priority=1,
            apply_to=PolicyApplyTo.QUEUES,
            definition=PolicyDefinition(dead_letter_exchange="policy-dlx", dead_letter_routing_key="policy-key"),
        ),
    )
    client.queues.create(
        "work", QueueRequest(arguments=ClassicQueueRequest(dead_letter_exchange="argument-dlx" if override else None))
    )
    wait_for(lambda: client.queues.get("work").policy == "dlx-policy", "policy selection")

    result = _coverage(client)

    assert result.status == DeadLetterCoverageStatus.DESTINATION_POSSIBLE
    assert result.dead_letter_exchange is not None
    assert result.dead_letter_exchange.name == ("argument-dlx" if override else "policy-dlx")
    assert result.routing_key == "policy-key"
    assert [node.name for node in result.destinations] == [result.dead_letter_exchange.name]


def test_default_exchange_is_a_valid_dlx(
    live_client: RabbitMQClient, live_vhost: str, management_api: ManagementAPI
) -> None:
    client = live_client
    client.queues.create("parking", QueueRequest(arguments=ClassicQueueRequest()))
    client.queues.create(
        "work", QueueRequest(arguments=ClassicQueueRequest(dead_letter_exchange="", dead_letter_routing_key="parking"))
    )

    assert _coverage(client).status == DeadLetterCoverageStatus.DESTINATION_POSSIBLE
    assert management_api.publish(live_vhost, "amq.default", "work", "default-dlx")
    wait_for(
        lambda: management_api.messages(live_vhost, "work", ackmode="reject_requeue_false"), "default DLX rejection"
    )
    management_api.delivered(live_vhost, "parking", "default-dlx")
