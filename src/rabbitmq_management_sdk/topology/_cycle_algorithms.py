"""Iterative graph algorithms used by topology cycle analysis."""

from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass

from rabbitmq_management_sdk.topology.models import NodeId
from rabbitmq_management_sdk.topology.ordering import NodeSortKey, node_sort_key

Adjacency = dict[NodeId, tuple[NodeId, ...]]
CyclePathFilter = Callable[[Sequence[NodeId]], bool]


def _component_sort_key(component: frozenset[NodeId]) -> tuple[NodeSortKey, ...]:
    """Return a deterministic ordering key for one component."""
    return tuple(node_sort_key(node) for node in sorted(component, key=node_sort_key))


def _induced_adjacency(
    vertices: Iterable[NodeId],
    adjacency: Mapping[NodeId, Sequence[NodeId]],
) -> Adjacency:
    """Restrict adjacency to edges whose endpoints are both in ``vertices``."""
    vertex_set = frozenset(vertices)
    return {
        node: tuple(sorted((child for child in adjacency.get(node, ()) if child in vertex_set), key=node_sort_key))
        for node in sorted(vertex_set, key=node_sort_key)
    }


@dataclass(slots=True)
class _TarjanFrame:
    """Explicit call frame for the iterative Tarjan traversal."""

    node: NodeId
    children: Iterator[NodeId]


def _tarjan(
    vertices: Iterable[NodeId],
    adjacency: Mapping[NodeId, Sequence[NodeId]],
) -> Iterator[frozenset[NodeId]]:
    """Yield strongly connected components using iterative Tarjan DFS.

    The explicit work stack avoids Python's recursion limit for long topology
    paths. Traversal is sorted so component discovery remains deterministic.
    """
    vertex_set = frozenset(vertices)
    index_counter = 0
    index: dict[NodeId, int] = {}
    lowlink: dict[NodeId, int] = {}
    on_stack: set[NodeId] = set()
    tarjan_stack: list[NodeId] = []

    def children_for(node: NodeId) -> Iterator[NodeId]:
        return iter(sorted((child for child in adjacency.get(node, ()) if child in vertex_set), key=node_sort_key))

    for start in sorted(vertex_set, key=node_sort_key):
        if start in index:
            continue

        work = [_TarjanFrame(start, children_for(start))]
        index[start] = lowlink[start] = index_counter
        index_counter += 1
        tarjan_stack.append(start)
        on_stack.add(start)

        while work:
            frame = work[-1]
            descended = False

            for child in frame.children:
                if child not in index:
                    # Tree edge: discover the child and suspend this frame
                    # with its remaining iterator intact.
                    index[child] = lowlink[child] = index_counter
                    index_counter += 1
                    tarjan_stack.append(child)
                    on_stack.add(child)
                    work.append(_TarjanFrame(child, children_for(child)))
                    descended = True
                    break
                if child in on_stack:
                    # Only a still-open node can lower this component's
                    # lowlink. A completed child already belongs to another
                    # sealed component.
                    lowlink[frame.node] = min(lowlink[frame.node], index[child])

            if descended:
                continue

            work.pop()
            if work:
                # Equivalent to returning lowlink from a recursive child.
                parent = work[-1].node
                lowlink[parent] = min(lowlink[parent], lowlink[frame.node])

            if lowlink[frame.node] == index[frame.node]:
                # This frame is a SCC root; all active nodes discovered
                # after it belong to the same component.
                component: list[NodeId] = []
                while True:
                    node = tarjan_stack.pop()
                    on_stack.remove(node)
                    component.append(node)
                    if node == frame.node:
                        break
                yield frozenset(component)


def _is_cyclic_component(
    component: frozenset[NodeId],
    adjacency: Mapping[NodeId, Sequence[NodeId]],
) -> bool:
    """Return whether a component has multiple nodes or a self-loop."""
    return len(component) > 1 or any(node in adjacency.get(node, ()) for node in component)


@dataclass(slots=True)
class _CircuitFrame:
    """Explicit call frame for Johnson's iterative circuit traversal."""

    node: NodeId
    children: Iterator[NodeId]
    found: bool = False


def _circuit(
    least: NodeId,
    scc_adjacency: Mapping[NodeId, Sequence[NodeId]],
    *,
    cycle_filter: CyclePathFilter | None,
    max_cycles: int | None,
) -> tuple[list[tuple[NodeId, ...]], bool]:
    """Find elementary cycles returning to ``least``.

    Returns the accepted node paths and whether the requested result limit
    stopped the search after another matching cycle was found.
    """
    cycles: list[tuple[NodeId, ...]] = []
    blocked: set[NodeId] = set()
    block_map: defaultdict[NodeId, set[NodeId]] = defaultdict(set)
    path: list[NodeId] = []
    work: list[_CircuitFrame] = []

    def push(node: NodeId) -> None:
        path.append(node)
        blocked.add(node)
        work.append(_CircuitFrame(node, iter(scc_adjacency.get(node, ()))))

    def unblock(start: NodeId) -> None:
        # Unblocking cascades through nodes retained only because ``start``
        # was blocked. An explicit stack keeps that cascade iterative.
        stack = [start]
        while stack:
            node = stack.pop()
            if node not in blocked:
                continue
            blocked.remove(node)
            for blocked_by in block_map.pop(node, set()):
                if blocked_by in blocked:
                    stack.append(blocked_by)

    push(least)
    while work:
        frame = work[-1]
        descended = False

        for child in frame.children:
            if child == least:
                # The current path is one elementary cycle. It continues through
                # the remaining children because this node may close others.
                node_path = tuple(path)
                if cycle_filter is None or cycle_filter(node_path):
                    if max_cycles is not None and len(cycles) >= max_cycles:
                        return cycles, True
                    cycles.append(node_path)
                frame.found = True
            elif child not in blocked:
                push(child)
                descended = True
                break

        if descended:
            continue

        work.pop()
        if frame.found:
            unblock(frame.node)
        else:
            # Preserve the dependency so this failed node is reconsidered if
            # one of its children is later unblocked.
            for child in scc_adjacency.get(frame.node, ()):
                block_map[child].add(frame.node)
        path.pop()

        if work and frame.found:
            work[-1].found = True

    return cycles, False


def _johnson(
    component: frozenset[NodeId],
    adjacency: Mapping[NodeId, Sequence[NodeId]],
    *,
    cycle_filter: CyclePathFilter | None,
    max_cycles: int | None,
) -> tuple[list[tuple[NodeId, ...]], bool]:
    """Enumerate elementary cycles within one cyclic component."""
    worklist: list[tuple[frozenset[NodeId], Adjacency]] = [(component, _induced_adjacency(component, adjacency))]
    all_cycles: list[tuple[NodeId, ...]] = []

    while worklist:
        vertices, local_adjacency = worklist.pop()
        if not vertices:
            continue

        least = min(vertices, key=node_sort_key)
        remaining_cycles = None if max_cycles is None else max_cycles - len(all_cycles)
        cycles, truncated = _circuit(
            least,
            local_adjacency,
            cycle_filter=cycle_filter,
            max_cycles=remaining_cycles,
        )
        all_cycles.extend(cycles)
        if truncated:
            return all_cycles, True

        remaining_vertices = vertices - {least}
        if not remaining_vertices:
            continue

        # Removing ``least`` can split the original SCC. Recompute SCCs and
        # enqueue only pieces that still contain a cycle.
        induced = _induced_adjacency(remaining_vertices, local_adjacency)
        subcomponents = [scc for scc in _tarjan(remaining_vertices, induced) if _is_cyclic_component(scc, induced)]
        for scc in sorted(subcomponents, key=_component_sort_key, reverse=True):
            worklist.append((scc, _induced_adjacency(scc, induced)))

    return all_cycles, False
