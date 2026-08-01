"""Deterministic ordering helpers shared by topology analysis and reports."""

from typing import NamedTuple

from rabbitmq_management_sdk.topology.models import NodeId, TopologyEdge

OptionalStringSortKey = tuple[int, str]


class NodeSortKey(NamedTuple):
    """Comparable, cluster-aware sort key for a :class:`NodeId`."""

    cluster_id: OptionalStringSortKey
    vhost: str
    name: str
    kind: str


class EdgeSortKey(NamedTuple):
    """Comparable, complete sort key for a :class:`TopologyEdge`."""

    source: NodeSortKey
    target: NodeSortKey
    kind: str
    routing_key: OptionalStringSortKey
    arguments: OptionalStringSortKey


def node_sort_key(node: NodeId) -> NodeSortKey:
    """Return the canonical node order across one or more clusters."""
    return NodeSortKey(
        cluster_id=optional_string_sort_key(node.cluster_id),
        vhost=node.vhost,
        name=node.name,
        kind=node.kind.value,
    )


def optional_string_sort_key(value: str | None) -> OptionalStringSortKey:
    """Order strings before an absent value without comparing str to None."""
    return (1, "") if value is None else (0, value)


def edge_sort_key(edge: TopologyEdge) -> EdgeSortKey:
    """Return a deterministic total order that distinguishes parallel edges."""
    return EdgeSortKey(
        source=node_sort_key(edge.source),
        target=node_sort_key(edge.target),
        kind=edge.kind.value,
        routing_key=optional_string_sort_key(edge.routing_key),
        arguments=optional_string_sort_key(edge.arguments),
    )
