"""Compare contextual exchange findings with actual broker publications."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from rabbitmq_management_sdk import BindingRequest, ClusterAuditor, ExchangeRequest
from rabbitmq_management_sdk.resources.v4.exchanges.schemas.exchange_request import ExchangeArguments
from rabbitmq_management_sdk.resources.v4.queues.schemas.queue_request import ClassicQueueRequest, QueueRequest
from rabbitmq_management_sdk.topology import RoutingStatus

if TYPE_CHECKING:
    from tests.shared.live_broker import ManagementAPI

    from rabbitmq_management_sdk import RabbitMQClient
    from rabbitmq_management_sdk.topology import ExchangeRoutingCoverage

pytestmark = pytest.mark.live


def _auditor(client: RabbitMQClient) -> ClusterAuditor:
    return ClusterAuditor(
        client.admin.export_definitions(),
        queues=client.queues.list_all(disable_stats=True),
        exchanges=client.exchanges.list_all(disable_stats=True),
    )


def _entry(auditor: ClusterAuditor, vhost: str, key: str | None = None) -> ExchangeRoutingCoverage:
    return next(
        result
        for result in auditor.exchange_routing_coverage(routing_key=key)
        if (result.exchange.id.vhost, result.exchange.id.name) == (vhost, "entry")
    )


def test_key_specific_findings_preserve_degree_based_black_holes(
    live_client: RabbitMQClient, live_vhost: str, management_api: ManagementAPI
) -> None:
    client = live_client
    client.exchanges.create("entry", ExchangeRequest(type="direct"))
    client.queues.create("sink", QueueRequest(arguments=ClassicQueueRequest()))
    client.bindings.create_exchange_to_queue("entry", "sink", BindingRequest(routing_key="accepted"))
    auditor = _auditor(client)

    assert _entry(auditor, live_vhost).status == RoutingStatus.UNDETERMINED
    assert _entry(auditor, live_vhost, "accepted").status == RoutingStatus.DESTINATION_POSSIBLE
    assert _entry(auditor, live_vhost, "rejected").status == RoutingStatus.NO_DESTINATION
    assert _entry(auditor, live_vhost).exchange.id not in {exchange.id for exchange in auditor.black_hole_exchanges()}
    assert auditor.audit().exchange_routing_coverage == auditor.exchange_routing_coverage()
    assert management_api.publish(live_vhost, "entry", "accepted", "accepted-publication")
    management_api.delivered(live_vhost, "sink", "accepted-publication")
    assert not management_api.publish(live_vhost, "entry", "rejected", "unroutable-publication")


def test_exchange_bindings_can_lead_to_no_queue_destination(
    live_client: RabbitMQClient, live_vhost: str, management_api: ManagementAPI
) -> None:
    client = live_client
    for name in ("entry", "next"):
        client.exchanges.create(name, ExchangeRequest(type="direct"))
    client.queues.create("sink", QueueRequest(arguments=ClassicQueueRequest()))
    client.bindings.create_exchange_to_exchange("entry", "next", BindingRequest(routing_key="first"))
    client.bindings.create_exchange_to_queue("next", "sink", BindingRequest(routing_key="second"))
    auditor = _auditor(client)

    assert _entry(auditor, live_vhost, "first").status == RoutingStatus.NO_DESTINATION
    assert _entry(auditor, live_vhost, "second").status == RoutingStatus.NO_DESTINATION
    assert _entry(auditor, live_vhost).status == RoutingStatus.UNDETERMINED
    assert not management_api.publish(live_vhost, "entry", "first", "first-key")
    assert not management_api.publish(live_vhost, "entry", "second", "second-key")


def test_alternate_exchange_activation_depends_on_the_publication_key(
    live_client: RabbitMQClient, live_vhost: str, management_api: ManagementAPI
) -> None:
    client = live_client
    client.exchanges.create("fallback", ExchangeRequest(type="fanout"))
    client.exchanges.create("dead-end", ExchangeRequest(type="direct"))
    client.exchanges.create(
        "entry", ExchangeRequest(type="direct", arguments=ExchangeArguments(alternate_exchange="fallback"))
    )
    client.queues.create("sink", QueueRequest(arguments=ClassicQueueRequest()))
    client.bindings.create_exchange_to_queue("fallback", "sink", BindingRequest())
    client.bindings.create_exchange_to_exchange("entry", "dead-end", BindingRequest(routing_key="accepted"))
    auditor = _auditor(client)

    assert _entry(auditor, live_vhost, "accepted").status == RoutingStatus.NO_DESTINATION
    assert _entry(auditor, live_vhost, "other").status == RoutingStatus.DESTINATION_POSSIBLE
    assert not management_api.publish(live_vhost, "entry", "accepted", "suppressed-alternate")
    assert management_api.publish(live_vhost, "entry", "other", "alternate-publication")
    management_api.delivered(live_vhost, "sink", "alternate-publication")
