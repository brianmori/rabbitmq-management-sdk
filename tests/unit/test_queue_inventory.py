"""Check queue inventory independently of routing and runtime activity."""

from dataclasses import replace

import pytest
from tests.shared.parser_fixtures import _exchange, _queue, _response, _vhost

from rabbitmq_management_sdk import ClusterAuditor
from rabbitmq_management_sdk.topology import (
    ClusterTopology,
    EdgeKind,
    ExchangeNode,
    NodeId,
    NodeKind,
    QueueNode,
    QueueTypeInventory,
    TopologyEdge,
)

pytestmark = pytest.mark.unit


def test_inventory_groups_resolved_types_separately_from_vhost_defaults() -> None:
    auditor = ClusterAuditor(
        _response(
            vhosts=[_vhost("b", "quorum"), _vhost("empty", "stream"), _vhost("a", None)],
            queues=[
                _queue("same", "b", **{"x-queue-type": "classic"}),
                _queue("stream", "a", **{"x-queue-type": "stream"}),
                _queue("same", "a", **{"x-queue-type": "classic"}),
                _queue("defaulted", "b"),
            ],
        )
    )

    inventory = auditor.queue_type_inventory()

    assert inventory.queue_count == 4
    assert [(group.queue_type, group.queue_count) for group in inventory.queue_types] == [
        ("classic", 2),
        ("quorum", 1),
        ("stream", 1),
    ]
    assert [(queue.id.vhost, queue.id.name) for queue in inventory.queue_types[0].queues] == [
        ("a", "same"),
        ("b", "same"),
    ]
    assert [(vhost.vhost, vhost.default_queue_type, vhost.queue_count) for vhost in inventory.vhosts] == [
        ("a", None, 2),
        ("b", "quorum", 2),
        ("empty", "stream", 0),
    ]
    assert [[group.queue_type for group in vhost.queue_types] for vhost in inventory.vhosts] == [
        ["classic", "stream"],
        ["classic", "quorum"],
        [],
    ]
    assert {queue for group in inventory.queue_types for queue in group.queues} == auditor.topology.queues


def test_resource_vhosts_without_metadata_have_unknown_defaults() -> None:
    auditor = ClusterAuditor(
        _response(
            exchanges=[_exchange("only-exchange", "exchange-only")],
            queues=[_queue("q", "queue-only", **{"x-queue-type": "quorum"})],
        )
    )

    inventory = auditor.queue_type_inventory()

    assert [(vhost.vhost, vhost.default_queue_type, vhost.queue_count) for vhost in inventory.vhosts] == [
        ("exchange-only", None, 0),
        ("queue-only", None, 1),
    ]
    assert auditor.topology.vhosts == frozenset()


def test_dangling_routes_do_not_create_inventory_entries() -> None:
    exchange = ExchangeNode(NodeId(vhost="v", name="e", kind=NodeKind.EXCHANGE), "direct", False, True)
    edge = TopologyEdge(exchange.id, NodeId(vhost="v", name="missing", kind=NodeKind.QUEUE), EdgeKind.BINDING)
    topology = ClusterTopology(frozenset({exchange}), frozenset(), frozenset(), frozenset({edge}))
    auditor = ClusterAuditor.from_topology(topology)

    inventory = auditor.queue_type_inventory()

    assert inventory.queue_count == 0
    assert inventory.queue_types == ()
    assert inventory.vhosts[0].queue_count == 0
    assert auditor.dangling_edges() == (edge,)
    without_nodes = replace(topology, exchanges=frozenset())
    assert ClusterAuditor.from_topology(without_nodes).queue_type_inventory() == QueueTypeInventory()


def test_manually_constructed_graph_preserves_open_queue_type_names() -> None:
    queue = QueueNode(NodeId(cluster_id="cluster", vhost="v", name="q", kind=NodeKind.QUEUE), "plugin-queue", True)
    topology = ClusterTopology(frozenset(), frozenset({queue}), frozenset(), frozenset(), cluster_id="cluster")

    inventory = ClusterAuditor.from_topology(topology).queue_type_inventory()

    assert inventory.queue_count == 1
    assert inventory.queue_types[0].queue_type == "plugin-queue"
    assert inventory.queue_types[0].queues == (queue,)
    assert inventory.vhosts[0].default_queue_type is None


def test_input_order_does_not_change_inventory() -> None:
    vhosts = [_vhost("z"), _vhost("a", "quorum")]
    queues = [
        _queue(name, vhost, **{"x-queue-type": kind})
        for name, vhost, kind in (("z", "z", "classic"), ("a", "a", "stream"), ("a", "z", "classic"))
    ]
    first = ClusterAuditor(_response(vhosts=vhosts, queues=queues))
    reversed_input = ClusterAuditor(_response(vhosts=vhosts[::-1], queues=queues[::-1]))

    assert first.queue_type_inventory() == reversed_input.queue_type_inventory()


def test_empty_graph_has_an_empty_inventory() -> None:
    assert ClusterAuditor(_response()).queue_type_inventory() == QueueTypeInventory()
