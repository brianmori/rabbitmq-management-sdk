"""Tests for degree-based facts over captured topology configuration."""

import pytest

from rabbitmq_management_sdk.topology.models import (
    ClusterTopology,
    EdgeKind,
    ExchangeNode,
    NodeId,
    NodeKind,
    QueueNode,
    TopologyEdge,
)
from rabbitmq_management_sdk.topology.reachability import (
    black_hole_exchanges,
    queues_without_declared_ingress,
    unreachable_internal_exchanges,
)

pytestmark = pytest.mark.unit


def _exchange(name: str, *, internal: bool = False) -> ExchangeNode:
    return ExchangeNode(
        id=NodeId(vhost="v", name=name, kind=NodeKind.EXCHANGE),
        exchange_type="direct",
        internal=internal,
        durable=True,
    )


def _queue(name: str) -> QueueNode:
    return QueueNode(
        id=NodeId(vhost="v", name=name, kind=NodeKind.QUEUE),
        queue_type="classic",
        durable=True,
    )


def _topology(
    *,
    exchanges: frozenset[ExchangeNode] = frozenset(),
    queues: frozenset[QueueNode] = frozenset(),
    edges: frozenset[TopologyEdge] = frozenset(),
) -> ClusterTopology:
    return ClusterTopology(
        exchanges=exchanges,
        queues=queues,
        shovels=frozenset(),
        edges=edges,
    )


def test_default_exchange_is_not_reported_as_a_black_hole() -> None:
    default_exchange = _exchange("")
    topology = _topology(exchanges=frozenset({default_exchange}))

    assert black_hole_exchanges(topology) == ()


def test_route_to_an_undeclared_target_counts_as_outgoing_evidence() -> None:
    declared_exchange = _exchange("events")
    missing_queue = NodeId(vhost="v", name="not-in-export", kind=NodeKind.QUEUE)
    route = TopologyEdge(
        source=declared_exchange.id,
        target=missing_queue,
        kind=EdgeKind.BINDING,
        routing_key="events",
    )
    topology = _topology(
        exchanges=frozenset({declared_exchange}),
        edges=frozenset({route}),
    )

    assert black_hole_exchanges(topology) == ()
    assert topology.dangling_edges() == frozenset({route})


def test_routes_from_an_undeclared_source_count_as_incoming_evidence() -> None:
    internal_exchange = _exchange("internal-events", internal=True)
    queue = _queue("events")
    missing_exchange = NodeId(vhost="v", name="not-in-export", kind=NodeKind.EXCHANGE)
    exchange_route = TopologyEdge(
        source=missing_exchange,
        target=internal_exchange.id,
        kind=EdgeKind.BINDING,
        routing_key="internal",
    )
    queue_route = TopologyEdge(
        source=missing_exchange,
        target=queue.id,
        kind=EdgeKind.BINDING,
        routing_key="queue",
    )
    topology = _topology(
        exchanges=frozenset({internal_exchange}),
        queues=frozenset({queue}),
        edges=frozenset({exchange_route, queue_route}),
    )

    assert unreachable_internal_exchanges(topology) == ()
    assert queues_without_declared_ingress(topology) == ()
    assert topology.dangling_edges() == frozenset({exchange_route, queue_route})
