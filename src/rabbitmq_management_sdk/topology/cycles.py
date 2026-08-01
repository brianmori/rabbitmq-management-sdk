"""Assemble deterministic cycle analysis for :class:`ClusterTopology`.

Tarjan's algorithm partitions the contained-node graph into strongly connected
components. Johnson's algorithm then enumerates elementary cycles only inside
components capable of containing them. Both traversals are iterative.

Edges with an endpoint absent from the topology are excluded because they
cannot form a cycle among contained nodes; :meth:`ClusterTopology.dangling_edges`
reports them separately. Reported cycles are exact directed node cycles in the
captured configuration graph, with one deterministic representative edge for
each hop. They do not guarantee that a particular message can satisfy every
routing or runtime condition along the path.

The graph-algorithm mechanics live in ``_cycle_algorithms.py``. This module
owns the result values, RabbitMQ-edge reconstruction, filtering, limits, and
deterministic report ordering.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from rabbitmq_management_sdk.exceptions import TopologyAnalysisError, TopologyValidationError
from rabbitmq_management_sdk.topology._cycle_algorithms import (
    Adjacency,
    CyclePathFilter,
    _component_sort_key,
    _is_cyclic_component,
    _johnson,
    _tarjan,
)
from rabbitmq_management_sdk.topology.models import (
    ClusterTopology,
    EdgeKind,
    NodeId,
    TopologyEdge,
)
from rabbitmq_management_sdk.topology.ordering import NodeSortKey, edge_sort_key, node_sort_key

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


@dataclass(frozen=True, slots=True)
class StronglyConnectedComponent:
    """One member of the complete SCC partition of a topology graph.

    ``nodes`` contains identities rather than duplicating topology records. A
    component is cyclic when it contains multiple nodes or its only node has a
    self-loop. Components may span clusters when produced from a future
    federated graph; single-cluster consistency is enforced by
    :class:`ClusterTopology` instead.
    """

    nodes: frozenset[NodeId]
    is_cyclic: bool

    def __post_init__(self) -> None:
        """Validate component membership."""
        if not isinstance(self.nodes, frozenset) or not self.nodes:
            raise TopologyValidationError(
                "StronglyConnectedComponent.nodes must be a non-empty frozenset of NodeId values"
            )
        for node in self.nodes:
            if not isinstance(node, NodeId):
                raise TopologyValidationError(f"StronglyConnectedComponent.nodes must be NodeId, got {node!r}")
        if not isinstance(self.is_cyclic, bool):
            raise TopologyValidationError(f"StronglyConnectedComponent.is_cyclic must be bool, got {self.is_cyclic!r}")


@dataclass(frozen=True, slots=True)
class Cycle:
    """An elementary cycle.

    ``edges`` is the single stored representation of the path. ``nodes`` is
    derived from the ordered edge sources, preventing the two views from
    drifting apart. No node repeats except the implicit final return to
    ``nodes[0]``. When the topology has parallel edges between two nodes, cycle
    analysis stores the first edge in deterministic order as their
    representative.
    """

    edges: tuple[TopologyEdge, ...]

    def __post_init__(self) -> None:
        """Validate that the ordered edges form one elementary cycle."""
        if not isinstance(self.edges, tuple) or not self.edges:
            raise TopologyValidationError("Cycle.edges must be a non-empty tuple of TopologyEdge values")
        for edge in self.edges:
            if not isinstance(edge, TopologyEdge):
                raise TopologyValidationError(f"Cycle.edges must contain only TopologyEdge values, got {edge!r}")

        nodes = self.nodes
        if len(set(nodes)) != len(nodes):
            raise TopologyValidationError("Cycle.edges must describe an elementary cycle without repeated nodes")
        for index, edge in enumerate(self.edges):
            next_node = nodes[(index + 1) % len(nodes)]
            if edge.target != next_node:
                raise TopologyValidationError(f"Cycle edge {edge!r} does not connect to {next_node!r}")

    @property
    def nodes(self) -> tuple[NodeId, ...]:
        """Return the cycle's nodes in traversal order."""
        return tuple(edge.source for edge in self.edges)

    def __len__(self) -> int:
        """Return the number of hops in the cycle."""
        return len(self.edges)

    def __str__(self) -> str:
        """Return the closed node path as a readable arrow sequence."""
        nodes = self.nodes
        return " -> ".join(str(node) for node in (*nodes, nodes[0]))


@dataclass(frozen=True, slots=True)
class CycleSearchResult:
    """The cycles selected by one topology analysis.

    ``truncated`` is true only when ``max_cycles`` stopped the search after
    finding at least one additional matching cycle. Cycle-search entry points
    interpret a limit of zero as unlimited.
    """

    cycles: tuple[Cycle, ...]
    truncated: bool

    def __post_init__(self) -> None:
        """Validate the result collection and truncation flag."""
        if not isinstance(self.cycles, tuple):
            raise TopologyValidationError("CycleSearchResult.cycles must be a tuple of Cycle values")
        for cycle in self.cycles:
            if not isinstance(cycle, Cycle):
                raise TopologyValidationError(f"CycleSearchResult.cycles must contain only Cycle values, got {cycle!r}")
        if not isinstance(self.truncated, bool):
            raise TopologyValidationError(f"CycleSearchResult.truncated must be bool, got {self.truncated!r}")


def _path_sort_key(path: Sequence[NodeId]) -> tuple[NodeSortKey, ...]:
    return tuple(node_sort_key(node) for node in path)


def _adjacency(topology: ClusterTopology) -> Adjacency:
    """Build the contained-node, reachability-only view of a topology graph.

    The returned mapping has an entry for every contained node, even when it
    has no targets. Each target tuple contains each reachable next node once,
    in deterministic order. For example, two otherwise-distinct ``A -> B``
    bindings produce ``A: (B,)`` because the cycle algorithms enumerate node
    paths, not individual RabbitMQ binding variants.

    Edges whose source or target is not contained in the topology are
    deliberately omitted from cycle detection and remain visible via
    ``dangling_edges()``. ``_find_cycles`` separately prepares one
    deterministic representative edge for every contained node pair.
    """
    vertices = topology.all_node_ids
    targets_by_source: dict[NodeId, set[NodeId]] = {node: set() for node in vertices}

    # Build with sets first for O(1) membership + automatic de-dup of
    # parallel edges, then freeze into the sorted tuples every
    # algorithm below expects for deterministic traversal.
    for edge in topology.edges:
        if edge.source not in vertices or edge.target not in vertices:
            continue
        targets_by_source[edge.source].add(edge.target)

    return {
        source: tuple(sorted(targets, key=node_sort_key))
        for source, targets in sorted(targets_by_source.items(), key=lambda item: node_sort_key(item[0]))
    }


def _find_strongly_connected_components(
    topology: ClusterTopology,
    adjacency: Mapping[NodeId, Sequence[NodeId]],
) -> tuple[StronglyConnectedComponent, ...]:
    """Return the complete SCC partition using an existing adjacency view."""
    return tuple(
        StronglyConnectedComponent(
            nodes=component,
            is_cyclic=_is_cyclic_component(component, adjacency),
        )
        for component in sorted(
            _tarjan(topology.all_node_ids, adjacency),
            key=_component_sort_key,
        )
    )


def find_strongly_connected_components(
    topology: ClusterTopology,
) -> tuple[StronglyConnectedComponent, ...]:
    """Return the complete deterministic SCC partition of contained nodes.

    Every contained topology node appears in exactly one component. This
    includes acyclic singleton components; ``is_cyclic`` distinguishes them
    from multi-node components and self-looping singleton components.
    Dangling edge endpoints are not contained nodes and therefore do not appear.
    """
    adjacency = _adjacency(topology)
    return _find_strongly_connected_components(topology, adjacency)


def find_cyclic_components(
    topology: ClusterTopology,
) -> tuple[StronglyConnectedComponent, ...]:
    """Return only SCCs that contain at least one directed cycle."""
    return tuple(component for component in find_strongly_connected_components(topology) if component.is_cyclic)


def has_cycle(topology: ClusterTopology) -> bool:
    """Return True as soon as the first cyclic SCC is found."""
    adjacency = _adjacency(topology)
    return any(_is_cyclic_component(component, adjacency) for component in _tarjan(topology.all_node_ids, adjacency))


def _edges_for_cycle(
    node_path: Sequence[NodeId],
    representative_edges: Mapping[tuple[NodeId, NodeId], TopologyEdge],
) -> tuple[TopologyEdge, ...]:
    """Return the precomputed representative edge for each path hop."""
    path_len = len(node_path)
    return tuple(
        representative_edges[(source, node_path[(index + 1) % path_len])] for index, source in enumerate(node_path)
    )


def _representative_edges(
    topology: ClusterTopology,
    adjacency: Mapping[NodeId, Sequence[NodeId]],
) -> dict[tuple[NodeId, NodeId], TopologyEdge]:
    """Precompute one deterministic contained edge for each ordered node pair."""
    representatives: dict[tuple[NodeId, NodeId], TopologyEdge] = {}
    for edge in sorted(topology.edges, key=edge_sort_key):
        if edge.source in adjacency and edge.target in adjacency:
            representatives.setdefault((edge.source, edge.target), edge)
    return representatives


def _cycle_sort_key(cycle: Cycle) -> tuple[NodeSortKey, ...]:
    return _path_sort_key(cycle.nodes)


def _validate_max_cycles(max_cycles: int) -> None:
    if isinstance(max_cycles, bool) or not isinstance(max_cycles, int) or max_cycles < 0:
        raise TopologyAnalysisError("max_cycles must be zero (unlimited) or a positive integer")


def _find_cycles(
    topology: ClusterTopology,
    *,
    max_cycles: int,
    cycle_filter: CyclePathFilter | None = None,
) -> CycleSearchResult:
    """Run one optionally bounded cycle search over the contained-node graph."""
    _validate_max_cycles(max_cycles)
    adjacency = _adjacency(topology)
    representative_edges = _representative_edges(topology, adjacency)

    node_paths: list[tuple[NodeId, ...]] = []
    truncated = False
    for component in _find_strongly_connected_components(topology, adjacency):
        if not component.is_cyclic:
            continue
        remaining = None if max_cycles == 0 else max_cycles - len(node_paths)
        paths, truncated = _johnson(
            component.nodes,
            adjacency,
            cycle_filter=cycle_filter,
            max_cycles=remaining,
        )
        node_paths.extend(paths)
        if truncated:
            break

    cycles = [
        Cycle(
            edges=_edges_for_cycle(node_path, representative_edges),
        )
        for node_path in node_paths
    ]
    return CycleSearchResult(cycles=tuple(sorted(cycles, key=_cycle_sort_key)), truncated=truncated)


def find_structural_cycles(topology: ClusterTopology, *, max_cycles: int = 0) -> CycleSearchResult:
    """Find elementary cycles in the captured configuration graph.

    A structural cycle is an exact directed node cycle through configured
    possible hops, represented by one deterministic edge for each node pair.
    It is not proof that a particular message can traverse the path, and
    RabbitMQ may suppress the corresponding delivery loop. Set ``max_cycles``
    to zero to return every result, or to a positive limit for a bounded
    result.
    """
    return _find_cycles(topology, max_cycles=max_cycles)


_MESSAGE_REPUBLISHING_KINDS = frozenset({EdgeKind.DEAD_LETTER, EdgeKind.SHOVEL})


def message_loop_candidates_from_complete_result(
    structural_cycles: CycleSearchResult,
) -> CycleSearchResult:
    """Filter a complete structural result without enumerating cycles again."""
    return CycleSearchResult(
        cycles=tuple(
            cycle
            for cycle in structural_cycles.cycles
            if any(edge.kind in _MESSAGE_REPUBLISHING_KINDS for edge in cycle.edges)
        ),
        truncated=False,
    )


def find_message_loop_candidates(topology: ClusterTopology, *, max_cycles: int = 0) -> CycleSearchResult:
    """Find structural cycles that cross a possible message-loop boundary.

    This classification requires at least one dead-letter or shovel hop.
    Dead-lettering can begin another routing operation, while a shovel crosses
    a consume-and-publish boundary. Binding and alternate-exchange hops alone
    do not meet that classification boundary. Results remain candidates, not
    proof that every routing key, header, dead-letter condition, and runtime
    state can sustain a loop.
    """
    declared_nodes = topology.all_node_ids
    republishing_pairs = frozenset(
        (edge.source, edge.target)
        for edge in topology.edges
        if edge.source in declared_nodes and edge.target in declared_nodes and edge.kind in _MESSAGE_REPUBLISHING_KINDS
    )

    def has_republishing_edge(node_path: Sequence[NodeId]) -> bool:
        return any(
            (source, node_path[(index + 1) % len(node_path)]) in republishing_pairs
            for index, source in enumerate(node_path)
        )

    return _find_cycles(
        topology,
        max_cycles=max_cycles,
        cycle_filter=has_republishing_edge,
    )
