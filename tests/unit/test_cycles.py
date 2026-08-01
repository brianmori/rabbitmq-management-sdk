import random
import time

import pytest

from rabbitmq_management_sdk import RabbitMQError, TopologyAnalysisError, TopologyValidationError
from rabbitmq_management_sdk.topology import Cycle, CycleSearchResult, StronglyConnectedComponent
from rabbitmq_management_sdk.topology.cycles import (
    find_cyclic_components,
    find_message_loop_candidates,
    find_strongly_connected_components,
    find_structural_cycles,
    has_cycle,
)
from rabbitmq_management_sdk.topology.models import (
    ClusterTopology,
    EdgeKind,
    ExchangeNode,
    NodeId,
    NodeKind,
    QueueNode,
    ResourceEndpoint,
    ShovelNode,
    TopologyEdge,
)
from rabbitmq_management_sdk.topology.ordering import node_sort_key

pytestmark = pytest.mark.unit


def _exchange(name: str, vhost: str = "v") -> NodeId:
    return NodeId(vhost=vhost, name=name, kind=NodeKind.EXCHANGE)


def _queue(name: str, vhost: str = "v") -> NodeId:
    return NodeId(vhost=vhost, name=name, kind=NodeKind.QUEUE)


def _shovel(name: str, vhost: str = "v") -> NodeId:
    return NodeId(vhost=vhost, name=name, kind=NodeKind.SHOVEL)


def _shovel_endpoint(vhost: str) -> ResourceEndpoint:
    return ResourceEndpoint(
        protocol="local",
        authorities=(),
        vhost=vhost,
        resource_name="unused",
        resource_kind=NodeKind.QUEUE,
        routing_key=None,
    )


def _edge(source: NodeId, target: NodeId, routing_key: str = "") -> TopologyEdge:
    return TopologyEdge(source=source, target=target, kind=EdgeKind.BINDING, routing_key=routing_key)


def _topology(edges: list[TopologyEdge], extra_nodes: tuple[NodeId, ...] = ()) -> ClusterTopology:
    nodes = {node for edge in edges for node in (edge.source, edge.target)} | set(extra_nodes)
    return ClusterTopology(
        exchanges=frozenset(
            ExchangeNode(id=node, exchange_type="direct", internal=False, durable=True)
            for node in nodes
            if node.kind == NodeKind.EXCHANGE
        ),
        queues=frozenset(
            QueueNode(id=node, queue_type="classic", durable=True) for node in nodes if node.kind == NodeKind.QUEUE
        ),
        shovels=frozenset(
            # Endpoint metadata is irrelevant to cycle detection (cycles
            # only ever looks at NodeId/TopologyEdge) -- filled in with the
            # node's own vhost purely so ShovelNode's own validation is satisfied.
            ShovelNode(
                id=node,
                source=_shovel_endpoint(node.vhost),
                destination=_shovel_endpoint(node.vhost),
            )
            for node in nodes
            if node.kind == NodeKind.SHOVEL
        ),
        edges=frozenset(edges),
    )


def _canonical(path: list[NodeId]) -> tuple[NodeId, ...]:
    min_index = min(enumerate(path), key=lambda item: node_sort_key(item[1]))[0]
    return tuple(path[min_index:] + path[:min_index])


def _brute_force_cycles(vertices: list[NodeId], edges: list[tuple[NodeId, NodeId]]) -> set[tuple[NodeId, ...]]:
    adjacency: dict[NodeId, list[NodeId]] = {node: [] for node in vertices}
    for source, target in edges:
        adjacency[source].append(target)

    found: set[tuple[NodeId, ...]] = set()

    def dfs(start: NodeId, current: NodeId, path: list[NodeId], visited: set[NodeId]) -> None:
        for target in adjacency.get(current, ()):
            if target == start:
                found.add(_canonical(path))
            elif target not in visited:
                dfs(start, target, [*path, target], visited | {target})

    for vertex in vertices:
        dfs(vertex, vertex, [vertex], {vertex})

    return found


class TestStronglyConnectedComponent:
    def test_holds_node_identity_without_copying_topology_records(self) -> None:
        node = NodeId(vhost="v", name="exchange", kind=NodeKind.EXCHANGE, cluster_id="cluster-a")

        component = StronglyConnectedComponent(nodes=frozenset({node}), is_cyclic=False)

        assert component.nodes == frozenset({node})

    def test_rejects_empty_component(self) -> None:
        with pytest.raises(TopologyValidationError, match="non-empty frozenset"):
            StronglyConnectedComponent(nodes=frozenset(), is_cyclic=False)

    def test_rejects_non_node_members(self) -> None:
        with pytest.raises(TopologyValidationError, match="must be NodeId"):
            StronglyConnectedComponent(nodes=frozenset({"not-a-node"}), is_cyclic=False)  # type: ignore[arg-type]

    def test_allows_nodes_from_different_clusters_for_federated_graphs(self) -> None:
        first = NodeId(vhost="v", name="a", kind=NodeKind.EXCHANGE, cluster_id="cluster-a")
        second = NodeId(vhost="v", name="b", kind=NodeKind.EXCHANGE, cluster_id="cluster-b")

        component = StronglyConnectedComponent(nodes=frozenset({first, second}), is_cyclic=True)

        assert component.nodes == frozenset({first, second})

    def test_rejects_non_boolean_cycle_flag(self) -> None:
        node = NodeId(vhost="v", name="exchange", kind=NodeKind.EXCHANGE)

        with pytest.raises(TopologyValidationError, match="is_cyclic must be bool"):
            StronglyConnectedComponent(nodes=frozenset({node}), is_cyclic=1)  # type: ignore[arg-type]


def test_declared_dead_letter_loop_is_detected_and_edges_are_preserved() -> None:
    queue = _queue("fan.q", "src")
    dlx = _exchange("my-fan.dlx", "src")
    topo = _topology(
        [
            TopologyEdge(source=queue, target=dlx, kind=EdgeKind.DEAD_LETTER, routing_key="failed"),
            TopologyEdge(source=dlx, target=queue, kind=EdgeKind.BINDING, routing_key=""),
        ]
    )

    assert has_cycle(topo) is True
    assert find_cyclic_components(topo) == (StronglyConnectedComponent(nodes=frozenset({queue, dlx}), is_cyclic=True),)

    (cycle,) = find_structural_cycles(topo).cycles
    assert cycle.nodes == (queue, dlx)
    assert {edge.kind for edge in cycle.edges} == {EdgeKind.DEAD_LETTER, EdgeKind.BINDING}
    assert next(edge for edge in cycle.edges if edge.kind == EdgeKind.DEAD_LETTER).routing_key == "failed"

    candidates = find_message_loop_candidates(topo)
    assert candidates == CycleSearchResult(cycles=(cycle,), truncated=False)


def test_exchange_only_cycle_is_structural_but_not_a_message_loop_candidate() -> None:
    a = _exchange("a")
    b = _exchange("b")
    topo = _topology([_edge(a, b), _edge(b, a)])

    structural = find_structural_cycles(topo)
    candidates = find_message_loop_candidates(topo)

    assert len(structural.cycles) == 1
    assert structural.truncated is False
    assert candidates == CycleSearchResult(cycles=(), truncated=False)


def test_cycle_through_all_three_node_kinds_is_detected() -> None:
    """A shovel feeding into a binding that routes back to the shovel's own source
    is a realistic pattern (e.g. a cross-vhost shovel whose destination
    eventually gets rebound to whatever it was reading from), so this pins
    down that mixed EXCHANGE/QUEUE/SHOVEL cycles are found correctly, not
    just same-kind ones."""
    queue = _queue("q1")
    shovel = _shovel("s1")
    exchange = _exchange("ex1")
    topo = _topology(
        [
            TopologyEdge(source=queue, target=shovel, kind=EdgeKind.SHOVEL),
            TopologyEdge(source=shovel, target=exchange, kind=EdgeKind.SHOVEL),
            TopologyEdge(source=exchange, target=queue, kind=EdgeKind.BINDING, routing_key="rk"),
        ]
    )

    assert has_cycle(topo) is True
    assert find_cyclic_components(topo) == (
        StronglyConnectedComponent(nodes=frozenset({queue, shovel, exchange}), is_cyclic=True),
    )

    (cycle,) = find_structural_cycles(topo).cycles
    assert cycle.nodes == (exchange, queue, shovel)
    assert [edge.kind for edge in cycle.edges] == [EdgeKind.BINDING, EdgeKind.SHOVEL, EdgeKind.SHOVEL]
    assert next(edge for edge in cycle.edges if edge.kind == EdgeKind.BINDING).routing_key == "rk"


def test_dangling_edges_are_not_treated_as_cycles() -> None:
    queue = _queue("q")
    missing_exchange = _exchange("missing")
    topo = ClusterTopology(
        exchanges=frozenset(),
        queues=frozenset({QueueNode(id=queue, queue_type="classic", durable=True)}),
        shovels=frozenset(),
        edges=frozenset(
            {
                TopologyEdge(source=queue, target=missing_exchange, kind=EdgeKind.DEAD_LETTER),
                TopologyEdge(source=missing_exchange, target=queue, kind=EdgeKind.BINDING),
            }
        ),
    )

    assert len(topo.dangling_edges()) == 2
    assert find_strongly_connected_components(topo) == (
        StronglyConnectedComponent(nodes=frozenset({queue}), is_cyclic=False),
    )
    assert find_cyclic_components(topo) == ()
    assert has_cycle(topo) is False
    assert find_structural_cycles(topo).cycles == ()


def test_scc_partition_includes_acyclic_singletons_and_self_loops() -> None:
    a, b, c, d, e = (_exchange(name) for name in "abcde")
    topo = _topology(
        [
            _edge(a, b),
            _edge(b, a),
            _edge(c, d),
            _edge(e, e),
        ]
    )

    components = find_strongly_connected_components(topo)

    assert components == (
        StronglyConnectedComponent(nodes=frozenset({a, b}), is_cyclic=True),
        StronglyConnectedComponent(nodes=frozenset({c}), is_cyclic=False),
        StronglyConnectedComponent(nodes=frozenset({d}), is_cyclic=False),
        StronglyConnectedComponent(nodes=frozenset({e}), is_cyclic=True),
    )
    assert frozenset(node for component in components for node in component.nodes) == topo.all_node_ids
    assert find_cyclic_components(topo) == (components[0], components[3])


def test_parallel_edges_do_not_duplicate_cycles() -> None:
    source = _exchange("a")
    target = _exchange("b")
    topo = _topology(
        [
            _edge(source, target, routing_key="rk1"),
            _edge(source, target, routing_key="rk2"),
            _edge(target, source, routing_key="back"),
        ]
    )

    cycles = find_structural_cycles(topo).cycles

    assert len(cycles) == 1
    assert cycles[0].nodes == (source, target)
    assert cycles[0].edges[0].routing_key == "rk1"


def test_cycle_report_order_is_deterministic() -> None:
    a, b, c, d, e = (_exchange(name) for name in "abcde")
    topo = _topology(
        [
            _edge(d, e),
            _edge(c, a),
            _edge(e, d),
            _edge(a, c),
            _edge(b, a),
            _edge(a, b),
        ]
    )

    assert [tuple(node.name for node in cycle.nodes) for cycle in find_structural_cycles(topo).cycles] == [
        ("a", "b"),
        ("a", "c"),
        ("d", "e"),
    ]


def test_cycle_limit_is_optional_and_reports_truncation() -> None:
    a, b, c, d, e = (_exchange(name) for name in "abcde")
    topo = _topology(
        [
            _edge(d, e),
            _edge(c, a),
            _edge(e, d),
            _edge(a, c),
            _edge(b, a),
            _edge(a, b),
        ]
    )

    limited = find_structural_cycles(topo, max_cycles=2)
    unlimited = find_structural_cycles(topo, max_cycles=0)

    assert len(limited.cycles) == 2
    assert limited.truncated is True
    assert len(unlimited.cycles) == 3
    assert unlimited.truncated is False


def test_negative_cycle_limit_raises_a_public_sdk_error() -> None:
    topology = _topology([])

    with pytest.raises(TopologyAnalysisError) as error:
        find_structural_cycles(topology, max_cycles=-1)

    assert isinstance(error.value, RabbitMQError)


@pytest.mark.parametrize("max_cycles", [True, 1.5, "1"])
def test_non_integer_cycle_limit_raises_a_public_sdk_error(max_cycles: object) -> None:
    with pytest.raises(TopologyAnalysisError):
        find_structural_cycles(_topology([]), max_cycles=max_cycles)  # type: ignore[arg-type]


def test_cycle_nodes_are_derived_from_ordered_edges() -> None:
    source = _exchange("source")
    target = _exchange("target")

    cycle = Cycle(edges=(_edge(source, target), _edge(target, source)))

    assert cycle.nodes == (source, target)


def test_cycle_model_rejects_an_empty_edge_path() -> None:
    with pytest.raises(TopologyValidationError, match="non-empty tuple"):
        Cycle(edges=())


def test_cycle_model_rejects_disconnected_edges() -> None:
    source = _exchange("source")
    target = _exchange("target")
    disconnected = _exchange("disconnected")

    with pytest.raises(TopologyValidationError, match="does not connect"):
        Cycle(edges=(_edge(source, target), _edge(disconnected, source)))


def test_self_loop_and_two_cycle_through_same_node_are_both_found() -> None:
    a = _exchange("a")
    b = _exchange("b")
    topo = _topology([_edge(a, a), _edge(a, b), _edge(b, a)])

    cycles = find_structural_cycles(topo).cycles

    assert [tuple(node.name for node in cycle.nodes) for cycle in cycles] == [("a",), ("a", "b")]


def test_randomized_small_graphs_match_brute_force() -> None:
    names = ["a", "b", "c", "d", "e"]
    vertices = [_exchange(name) for name in names]
    by_name = dict(zip(names, vertices, strict=True))
    possible_edges = [(source, target) for source in names for target in names if source != target]
    rng = random.Random(42)

    for _ in range(20):
        chosen = rng.sample(possible_edges, k=rng.randint(3, 9))
        edges = [_edge(by_name[source], by_name[target]) for source, target in chosen]
        topo = _topology(edges, extra_nodes=tuple(vertices))

        actual = {_canonical(list(cycle.nodes)) for cycle in find_structural_cycles(topo).cycles}
        expected = _brute_force_cycles(
            vertices,
            [(by_name[source], by_name[target]) for source, target in chosen],
        )

        assert actual == expected


def test_long_single_cycle_is_fast() -> None:
    node_count = 5000
    nodes = [_exchange(f"n{index:06d}") for index in range(node_count)]
    edges = [_edge(nodes[index], nodes[(index + 1) % node_count]) for index in range(node_count)]
    topo = _topology(edges)

    start = time.time()
    cycles = find_structural_cycles(topo).cycles
    elapsed = time.time() - start

    assert len(cycles) == 1
    assert len(cycles[0]) == node_count
    assert elapsed < 5.0
