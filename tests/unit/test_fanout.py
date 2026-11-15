"""Tests for direct fanout destination audits."""

from dataclasses import replace

import pytest

from rabbitmq_management_sdk.topology import (
    ClusterTopology,
    EdgeKind,
    ExchangeNode,
    FanoutExchangeBindings,
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
    assert groups[0].queue_count == 0
    assert groups[0].quorum_member_count == 0
    assert [queue.id.name for queue in groups[1].queues] == ["classic", "jobs", "plugin", "stream"]
    assert groups[1].queues[1].replication_factor == 3
    assert groups[2].queues[0].replication_factor == 5
    assert groups[1].queues[2].queue_type == "custom"
    assert all(queue.durable is True for group in groups for queue in group.queues)
    assert (groups[1].queue_count, groups[1].durable_queue_count, groups[1].quorum_queue_count) == (4, 4, 1)
    assert groups[1].quorum_member_count == groups[1].known_quorum_member_count == 3
    assert (groups[2].queue_count, groups[2].quorum_member_count) == (1, 5)


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
    assert group.queue_count == group.unknown_durability_count == group.unknown_queue_type_count == 1
    assert group.durable_queue_count == group.non_durable_queue_count == group.quorum_queue_count == 0
    assert group.known_quorum_member_count == 0
    assert group.quorum_member_count is None
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


@pytest.mark.parametrize("uncertainty", [None, "membership", "queue-type"])
def test_capacity_totals_keep_partial_membership_and_unknown_types_explicit(uncertainty: str | None) -> None:
    exchange = _exchange("v", "events")
    queues = {
        QueueNode(replace(exchange.id, name="quorum", kind=NodeKind.QUEUE), "quorum", True, 3),
        QueueNode(replace(exchange.id, name="classic", kind=NodeKind.QUEUE), "classic", False),
        QueueNode(replace(exchange.id, name="stream", kind=NodeKind.QUEUE), "stream", True),
    }
    if uncertainty == "membership":
        queues.add(QueueNode(replace(exchange.id, name="unobserved", kind=NodeKind.QUEUE), "quorum", True))
    targets = {queue.id for queue in queues}
    if uncertainty == "queue-type":
        targets.add(replace(exchange.id, name="missing", kind=NodeKind.QUEUE))
    topology = ClusterTopology(
        exchanges=frozenset({exchange}),
        queues=frozenset(queues),
        shovels=frozenset(),
        cluster_id="captured",
        edges=frozenset(
            TopologyEdge(exchange.id, target, EdgeKind.BINDING, key)
            for target in targets
            for key in ("first", "parallel")
        ),
    )

    (group,) = fanout_exchange_bindings(topology)

    assert group.queue_count == (3 if uncertainty is None else 4)
    assert group.durable_queue_count == (3 if uncertainty == "membership" else 2)
    assert group.non_durable_queue_count == 1
    assert group.unknown_durability_count == (1 if uncertainty == "queue-type" else 0)
    assert group.quorum_queue_count == (2 if uncertainty == "membership" else 1)
    assert group.unknown_queue_type_count == (1 if uncertainty == "queue-type" else 0)
    assert group.quorum_queues_without_membership_count == (1 if uncertainty == "membership" else 0)
    assert group.known_quorum_member_count == 3
    assert group.quorum_member_count == (3 if uncertainty is None else None)
    assert {queue.id.name: queue.durable for queue in group.queues}["classic"] is False


def test_known_non_quorum_types_have_zero_quorum_members() -> None:
    exchange = _exchange("v", "events")
    queues = frozenset(
        QueueNode(replace(exchange.id, name=kind, kind=NodeKind.QUEUE), kind, True)
        for kind in ("classic", "stream", "plugin-type")
    )
    topology = ClusterTopology(
        exchanges=frozenset({exchange}),
        queues=queues,
        shovels=frozenset(),
        cluster_id="captured",
        edges=frozenset(TopologyEdge(exchange.id, queue.id, EdgeKind.BINDING) for queue in queues),
    )

    (group,) = fanout_exchange_bindings(topology)

    assert group.queue_count == group.durable_queue_count == 3
    assert group.quorum_queue_count == group.quorum_queues_without_membership_count == 0
    assert group.known_quorum_member_count == group.quorum_member_count == 0
    assert group.unknown_queue_type_count == group.unknown_durability_count == 0


def test_omitted_durability_does_not_hide_known_quorum_members() -> None:
    exchange = _exchange("v", "events")
    queue = FanoutQueue(replace(exchange.id, name="q", kind=NodeKind.QUEUE), "quorum", 3)
    group = FanoutExchangeBindings(exchange, (queue,))
    assert queue.durable is None
    assert group.unknown_durability_count == 1
    assert group.known_quorum_member_count == group.quorum_member_count == 3
