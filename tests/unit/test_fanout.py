"""Tests for direct fanout destination audits."""

from dataclasses import replace

import pytest

from rabbitmq_management_sdk.topology import (
    ClusterTopology,
    EdgeKind,
    ExchangeNode,
    FanoutQueue,
    NodeId,
    NodeKind,
    QueueNode,
    TopologyEdge,
)
from rabbitmq_management_sdk.topology.fanout import fanout_exchange_bindings

pytestmark = pytest.mark.unit


def _exchange(vhost: str, name: str, exchange_type: str = "fanout") -> ExchangeNode:
    return ExchangeNode(
        id=NodeId(vhost=vhost, name=name, kind=NodeKind.EXCHANGE, cluster_id="captured"),
        exchange_type=exchange_type,
        internal=False,
        durable=True,
    )


def test_vhosts_distinct_queues_and_quorum_factors_are_preserved() -> None:
    exchanges = (_exchange("z", "events"), _exchange("a", "events"), _exchange("a", "empty"))
    queues = tuple(
        QueueNode(
            id=NodeId(vhost=vhost, name=name, kind=NodeKind.QUEUE, cluster_id="captured"),
            queue_type=queue_type,
            durable=True,
            replication_factor=factor,
        )
        for vhost, name, queue_type, factor in (
            ("z", "jobs", "quorum", 5),
            ("a", "jobs", "quorum", 3),
            ("a", "classic", "classic", None),
            ("a", "stream", "stream", None),
            ("a", "plugin", "custom", None),
        )
    )
    edges = frozenset(
        TopologyEdge(source=exchange.id, target=queue.id, kind=EdgeKind.BINDING, routing_key=key)
        for exchange in exchanges[:2]
        for queue in queues
        if exchange.id.vhost == queue.id.vhost
        for key in ("one", "two")
    )
    topology = ClusterTopology(
        exchanges=frozenset(exchanges),
        queues=frozenset(queues),
        shovels=frozenset(),
        edges=edges,
        cluster_id="captured",
    )

    groups = fanout_exchange_bindings(topology)

    assert [(group.exchange.id.vhost, group.exchange.id.name) for group in groups] == [
        ("a", "empty"),
        ("a", "events"),
        ("z", "events"),
    ]
    assert groups[0].queues == ()
    assert [queue.id.name for queue in groups[1].queues] == ["classic", "jobs", "plugin", "stream"]
    assert groups[1].queues[1].replication_factor == 3
    assert groups[2].queues[0].replication_factor == 5
    assert groups[1].queues[2].queue_type == "custom"


def test_only_direct_queue_bindings_count_and_dangling_targets_remain_visible() -> None:
    source = _exchange("v", "events")
    downstream = _exchange("v", "downstream", "direct")
    missing_source = _exchange("v", "absent")
    queue = QueueNode(id=replace(source.id, name="jobs", kind=NodeKind.QUEUE), queue_type="quorum", durable=True)
    dangling = replace(queue.id, name="missing")
    topology = ClusterTopology(
        exchanges=frozenset({source, downstream}),
        queues=frozenset({queue}),
        shovels=frozenset(),
        cluster_id="captured",
        edges=frozenset(
            {
                TopologyEdge(source=source.id, target=downstream.id, kind=EdgeKind.BINDING),
                TopologyEdge(source=downstream.id, target=queue.id, kind=EdgeKind.BINDING),
                TopologyEdge(source=source.id, target=dangling, kind=EdgeKind.BINDING),
                TopologyEdge(source=source.id, target=downstream.id, kind=EdgeKind.ALTERNATE_EXCHANGE),
                TopologyEdge(source=queue.id, target=source.id, kind=EdgeKind.DEAD_LETTER),
                TopologyEdge(source=missing_source.id, target=queue.id, kind=EdgeKind.BINDING),
            }
        ),
    )

    (group,) = fanout_exchange_bindings(topology)

    assert group.queues == (FanoutQueue(id=dangling),)
    assert dangling not in topology.nodes_by_id
    assert len(topology.dangling_edges()) == 2


def test_topology_without_fanout_exchanges_has_no_groups() -> None:
    topology = ClusterTopology(
        exchanges=frozenset({_exchange("v", "direct", "direct")}),
        queues=frozenset(),
        shovels=frozenset(),
        edges=frozenset(),
        cluster_id="captured",
    )
    assert fanout_exchange_bindings(topology) == ()
