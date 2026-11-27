"""Bounded traversal of configured edges in a captured topology."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from rabbitmq_management_sdk.exceptions import TopologyAnalysisError
from rabbitmq_management_sdk.topology.models import NodeId
from rabbitmq_management_sdk.topology.ordering import edge_sort_key, node_sort_key

if TYPE_CHECKING:
    from rabbitmq_management_sdk.topology.models import ClusterTopology, TopologyEdge

type TraceDirection = Literal["upstream", "downstream", "both"]


@dataclass(frozen=True, slots=True)
class TopologyTrace:
    """Exact graph membership and visible boundaries for a configured trace."""

    node_ids: tuple[NodeId, ...]
    edges: tuple[TopologyEdge, ...]
    upstream_ends: tuple[NodeId, ...]
    downstream_ends: tuple[NodeId, ...]
    upstream_cutoffs: tuple[NodeId, ...]
    downstream_cutoffs: tuple[NodeId, ...]


@dataclass(slots=True)
class _DirectionalTrace:
    nodes: set[NodeId] = field(default_factory=set)
    edges: set[TopologyEdge] = field(default_factory=set)
    ends: set[NodeId] = field(default_factory=set)
    limited_edges: dict[NodeId, list[TopologyEdge]] = field(default_factory=dict)

    def cutoffs(self, visible_edges: set[TopologyEdge]) -> tuple[NodeId, ...]:
        limited = (
            node for node, edges in self.limited_edges.items() if any(edge not in visible_edges for edge in edges)
        )
        return tuple(sorted(limited, key=node_sort_key))


def _trace_direction(
    topology: ClusterTopology,
    root: NodeId,
    max_hops: int | None,
    *,
    upstream: bool,
) -> _DirectionalTrace:
    adjacent: dict[NodeId, list[TopologyEdge]] = defaultdict(list)
    for edge in topology.edges:
        adjacent[edge.target if upstream else edge.source].append(edge)

    trace = _DirectionalTrace(nodes={root})
    pending = deque([(root, 0)])
    while pending:
        node, distance = pending.popleft()
        edges = adjacent.get(node)
        if not edges:
            trace.ends.add(node)
            continue
        if distance == max_hops:
            trace.limited_edges[node] = edges
            continue
        trace.edges.update(edges)
        for edge in edges:
            neighbor = edge.source if upstream else edge.target
            if neighbor not in trace.nodes:
                trace.nodes.add(neighbor)
                if neighbor in topology.all_node_ids:
                    pending.append((neighbor, distance + 1))
    return trace


def trace_topology(
    topology: ClusterTopology,
    resource: NodeId,
    *,
    direction: TraceDirection,
    max_hops: int | None,
) -> TopologyTrace:
    """Trace both directions independently on the same immutable graph."""
    if not isinstance(resource, NodeId) or resource not in topology.all_node_ids:
        raise TopologyAnalysisError("Trace requires the NodeId of a captured topology resource.")
    if direction not in ("upstream", "downstream", "both"):
        raise TopologyAnalysisError("Trace direction must be upstream, downstream, or both.")
    if max_hops is not None and (type(max_hops) is not int or max_hops < 1):
        raise TopologyAnalysisError("Trace depth must be a positive integer or None.")

    upstream = (
        _trace_direction(topology, resource, max_hops, upstream=True)
        if direction != "downstream"
        else _DirectionalTrace()
    )
    downstream = (
        _trace_direction(topology, resource, max_hops, upstream=False)
        if direction != "upstream"
        else _DirectionalTrace()
    )
    edges = upstream.edges | downstream.edges
    return TopologyTrace(
        node_ids=tuple(sorted(upstream.nodes | downstream.nodes, key=node_sort_key)),
        edges=tuple(sorted(edges, key=edge_sort_key)),
        upstream_ends=tuple(sorted(upstream.ends, key=node_sort_key)),
        downstream_ends=tuple(sorted(downstream.ends, key=node_sort_key)),
        upstream_cutoffs=upstream.cutoffs(edges),
        downstream_cutoffs=downstream.cutoffs(edges),
    )
