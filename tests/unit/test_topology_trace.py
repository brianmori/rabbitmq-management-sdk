"""Exercise configured route tracing through the public topology facade."""

import pytest
from tests.shared.parser_fixtures import _binding, _exchange, _queue, _response, _shovel_param, _vhost

from rabbitmq_management_sdk import ClusterAuditor, TopologyAnalysisError
from rabbitmq_management_sdk.topology import EdgeKind, NodeId, NodeKind, edge_sort_key, node_sort_key

pytestmark = pytest.mark.unit


def test_trace_follows_the_shovel_chain_without_switching_direction_at_shared_targets() -> None:
    captured = ClusterAuditor(
        _response(
            vhosts=[_vhost("source"), _vhost("target")],
            queues=[_queue("input", "source"), _queue("output", "target"), _queue("other-input", "source")],
            exchanges=[_exchange("fanout", "target", "fanout"), _exchange("other", "target")],
            bindings=[
                _binding("fanout", "output", "first", vhost="target"),
                _binding("fanout", "output", "second", vhost="target"),
                _binding("other", "output", vhost="target"),
            ],
            parameters=[
                _shovel_param(
                    name,
                    "source",
                    **{
                        "src-uri": "amqp:///source",
                        "src-queue": source,
                        "dest-uri": "amqp:///target",
                        "dest-exchange": "fanout",
                    },
                )
                for name, source in (("shovel", "input"), ("other-shovel", "other-input"))
            ],
        ),
    )
    auditor = ClusterAuditor.from_topology(captured.topology)
    root = NodeId("source", "input", NodeKind.QUEUE)
    shovel = NodeId("source", "shovel", NodeKind.SHOVEL)
    exchange = NodeId("target", "fanout", NodeKind.EXCHANGE)
    output = NodeId("target", "output", NodeKind.QUEUE)

    direct = auditor.trace_topology(root, direction="downstream", max_hops=1)
    assert set(direct.node_ids) == {root, shovel}
    assert direct.downstream_cutoffs == (shovel,)
    assert not direct.downstream_ends
    assert auditor.trace_topology(root, direction="downstream", max_hops=2).downstream_cutoffs == (exchange,)

    both = auditor.trace_topology(root, direction="both", max_hops=3)
    assert both.node_ids == tuple(sorted((root, shovel, exchange, output), key=node_sort_key))
    assert both.edges == tuple(sorted(both.edges, key=edge_sort_key))
    assert [edge.routing_key for edge in both.edges if edge.kind is EdgeKind.BINDING] == ["first", "second"]
    assert both.upstream_ends == (root,)
    assert both.downstream_ends == (output,)
    assert not both.downstream_cutoffs
    assert both == auditor.trace_topology(root, direction="both")

    inputs = auditor.trace_topology(exchange, direction="upstream", max_hops=1)
    assert {edge.source.name for edge in inputs.edges} == {"shovel", "other-shovel"}
    assert {node.name for node in inputs.upstream_cutoffs} == {"shovel", "other-shovel"}


def test_cycles_and_missing_endpoints_are_distinct_from_captured_path_ends() -> None:
    auditor = ClusterAuditor(
        _response(
            vhosts=[_vhost("v")],
            exchanges=[_exchange("a", "v"), _exchange("b", "v")],
            queues=[_queue("other", "v")],
            bindings=[
                _binding("a", "b", destination_type="exchange"),
                _binding("b", "a", destination_type="exchange"),
                _binding("b", "missing", destination_type="exchange"),
                _binding("missing", "other"),
            ],
        ),
    )
    root = NodeId("v", "a", NodeKind.EXCHANGE)
    missing = NodeId("v", "missing", NodeKind.EXCHANGE)
    trace = auditor.trace_topology(root, direction="downstream")
    assert set(trace.node_ids) == {root, NodeId("v", "b", NodeKind.EXCHANGE), missing}
    assert len(trace.edges) == 3
    assert not trace.downstream_ends
    assert not trace.downstream_cutoffs
    with pytest.raises(TopologyAnalysisError, match="captured topology resource"):
        auditor.trace_topology(missing, direction="upstream")
    with pytest.raises(TopologyAnalysisError, match="positive integer"):
        auditor.trace_topology(root, direction="upstream", max_hops=True)
