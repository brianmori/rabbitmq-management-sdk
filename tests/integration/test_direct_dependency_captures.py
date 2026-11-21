"""Replay RabbitMQ 4.2 and 4.3 captures through the dependency audit."""

from pathlib import Path

import pytest

from rabbitmq_management_sdk import ClusterAuditor
from rabbitmq_management_sdk.topology import DirectReference, DirectReferenceRole, NodeId, NodeKind

pytestmark = pytest.mark.integration
_CAPTURES = Path(__file__).parent


def test_rmq42_dependencies_use_broker_selected_dlx() -> None:
    capture = _CAPTURES / "rmq-4.2"
    auditor = ClusterAuditor.from_files(
        capture / "full-export-definitions.json",
        queues_path=capture / "queues-get-all-no-stats.json",
        exchanges_path=capture / "exchanges-get-all-no-stats.json",
    )
    dead_letter = NodeId("policy", "dlx.b", NodeKind.EXCHANGE, auditor.cluster_id)
    orders = NodeId("policy", "orders.q", NodeKind.QUEUE, auditor.cluster_id)
    inventory = auditor.direct_dependency_inventory()

    assert inventory.by_resource[dead_letter].references == (
        DirectReference(DirectReferenceRole.DEAD_LETTER_EXCHANGE, orders),
    )
    assert auditor.audit(max_cycles=1).direct_dependency_inventory == inventory


def test_rmq43_dependencies_retain_alternate_and_shovel_roles() -> None:
    capture = _CAPTURES / "rmq-4.3" / "complex-topology"
    auditor = ClusterAuditor.from_files(
        capture / "full-export-definitions.json",
        queues_path=capture / "queues.json",
        exchanges_path=capture / "exchanges.json",
        in_cluster_amqp_hosts={"127.0.0.1"},
    )
    fallback = NodeId("topology-demo.region-b", "complex.fallback", NodeKind.EXCHANGE, auditor.cluster_id)
    entry = NodeId("topology-demo.region-b", "complex.entry", NodeKind.EXCHANGE, auditor.cluster_id)
    work = NodeId("topology-demo.region-b", "complex.work.q", NodeKind.QUEUE, auditor.cluster_id)
    forward = NodeId("topology-demo.region-a", "complex.forward.q", NodeKind.QUEUE, auditor.cluster_id)
    dlx = NodeId("topology-demo.region-a", "complex.dlx", NodeKind.EXCHANGE, auditor.cluster_id)
    shovel = NodeId("topology-demo.control", "complex.ab", NodeKind.SHOVEL, auditor.cluster_id)
    by_resource = auditor.direct_dependency_inventory().by_resource

    assert by_resource[fallback].references == (
        DirectReference(DirectReferenceRole.ALTERNATE_EXCHANGE, entry),
        DirectReference(DirectReferenceRole.BINDING_SOURCE, work, ""),
    )
    assert by_resource[entry].references == (
        DirectReference(DirectReferenceRole.SHOVEL_DESTINATION, shovel, "demo.route"),
    )
    assert by_resource[forward].references == (
        DirectReference(DirectReferenceRole.BINDING_TARGET, dlx, ""),
        DirectReference(DirectReferenceRole.SHOVEL_SOURCE, shovel),
    )
