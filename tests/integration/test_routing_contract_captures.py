"""Evaluate routing contracts from RabbitMQ 4.2 and 4.3 captures."""

from pathlib import Path

import pytest

from rabbitmq_management_sdk import ClusterAuditor
from rabbitmq_management_sdk.topology import NodeId, NodeKind, RoutingCondition, RoutingContract, RoutingVerdict

pytestmark = pytest.mark.integration
_CAPTURES = Path(__file__).parent


def test_rmq42_fanout_reaches_only_its_captured_queue() -> None:
    capture = _CAPTURES / "rmq-4.2"
    auditor = ClusterAuditor.from_files(
        capture / "full-export-definitions.json",
        queues_path=capture / "queues-get-all-no-stats.json",
        exchanges_path=capture / "exchanges-get-all-no-stats.json",
    )
    origin = NodeId("src", "my-fan.dlx", NodeKind.EXCHANGE, auditor.cluster_id)
    queue = NodeId("src", "fan.q", NodeKind.QUEUE, auditor.cluster_id)
    contract = RoutingContract("only fan.q", origin, "", (RoutingCondition.exactly({queue}),))

    (result,) = auditor.evaluate_routing_contracts([contract])
    assert result.established_destinations == (queue,)
    assert result.verdict == RoutingVerdict.SATISFIED


def test_rmq43_fanout_distinguishes_reachability_and_exclusivity() -> None:
    capture = _CAPTURES / "rmq-4.3" / "complex-topology"
    auditor = ClusterAuditor.from_files(
        capture / "full-export-definitions.json",
        queues_path=capture / "queues.json",
        exchanges_path=capture / "exchanges.json",
    )
    vhost = "topology-demo.routing"
    origin = NodeId(vhost, "route.fanout", NodeKind.EXCHANGE, auditor.cluster_id)
    queue = NodeId(vhost, "route.classic", NodeKind.QUEUE, auditor.cluster_id)
    contracts = [
        RoutingContract("reaches classic", origin, "", (RoutingCondition.reaches_all({queue}),)),
        RoutingContract("only classic", origin, "", (RoutingCondition.exactly({queue}),)),
    ]

    reachability, exclusive = auditor.evaluate_routing_contracts(contracts)
    assert reachability.established_destinations == exclusive.established_destinations
    assert [node.name for node in reachability.established_destinations] == [
        "route.classic",
        "route.quorum",
        "route.stream",
    ]
    assert reachability.verdict == RoutingVerdict.SATISFIED
    assert exclusive.verdict == RoutingVerdict.VIOLATED
