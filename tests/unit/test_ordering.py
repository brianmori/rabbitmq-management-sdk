"""Tests for canonical topology ordering."""

import pytest

from rabbitmq_management_sdk.topology.models import EdgeKind, NodeId, NodeKind, TopologyEdge
from rabbitmq_management_sdk.topology.ordering import edge_sort_key, node_sort_key

pytestmark = pytest.mark.unit


def _node(cluster_id: str | None, name: str, kind: NodeKind = NodeKind.EXCHANGE) -> NodeId:
    return NodeId(
        cluster_id=cluster_id,
        vhost="v",
        name=name,
        kind=kind,
    )


def test_node_order_includes_cluster_identity() -> None:
    cluster_a = _node("cluster-a", "shared")
    cluster_b = _node("cluster-b", "shared")
    unscoped = _node(None, "shared")

    assert node_sort_key(cluster_a) != node_sort_key(cluster_b)
    assert node_sort_key(cluster_b) != node_sort_key(unscoped)
    assert sorted((unscoped, cluster_b, cluster_a), key=node_sort_key) == [
        cluster_a,
        cluster_b,
        unscoped,
    ]


def test_resource_coordinates_order_nodes_within_each_cluster() -> None:
    nodes = (
        _node("cluster-a", "b"),
        _node("cluster-a", "a", NodeKind.QUEUE),
        _node("cluster-a", "a"),
    )

    assert sorted(nodes, key=node_sort_key) == [
        _node("cluster-a", "a"),
        _node("cluster-a", "a", NodeKind.QUEUE),
        _node("cluster-a", "b"),
    ]


def test_edge_order_uses_the_same_cluster_aware_node_rule() -> None:
    def binding(cluster_id: str) -> TopologyEdge:
        return TopologyEdge(
            source=_node(cluster_id, "source"),
            target=_node(cluster_id, "target", NodeKind.QUEUE),
            kind=EdgeKind.BINDING,
            routing_key="",
        )

    cluster_a = binding("cluster-a")
    cluster_b = binding("cluster-b")

    assert sorted((cluster_b, cluster_a), key=edge_sort_key) == [cluster_a, cluster_b]
