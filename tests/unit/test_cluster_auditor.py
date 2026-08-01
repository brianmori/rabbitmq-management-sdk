"""Tests for the public ClusterAuditor facade and aggregate report."""

from pathlib import Path
from unittest.mock import patch

import pytest

from rabbitmq_management_sdk import (
    ClusterAuditor,
    RabbitMQError,
    TopologyAnalysisError,
    TopologyAuditReport,
    TopologyDefinitionsError,
    TopologyLoadError,
    TopologyResourceSnapshotError,
)
from rabbitmq_management_sdk.resources.v4.admin.schemas.export_response import ClusterDefinitionsResponse
from rabbitmq_management_sdk.resources.v4.exchanges.schemas.exchange_response import ExchangeResponse
from rabbitmq_management_sdk.topology import (
    ClusterTopology,
    CycleSearchResult,
    EdgeKind,
    StronglyConnectedComponent,
)
from rabbitmq_management_sdk.topology._cycle_algorithms import _johnson

pytestmark = pytest.mark.unit


def _definitions() -> ClusterDefinitionsResponse:
    return ClusterDefinitionsResponse.model_validate(
        {
            "users": [],
            "vhosts": [
                {
                    "name": "audit",
                    "description": "",
                    "tags": [],
                    "metadata": {"description": "", "tags": [], "default_queue_type": "classic"},
                }
            ],
            "permissions": [],
            "topic_permissions": [],
            "global_parameters": [],
            "parameters": [],
            "policies": [],
            "queues": [
                {
                    "name": "cycle-queue",
                    "vhost": "audit",
                    "durable": True,
                    "auto_delete": False,
                    "arguments": {"x-queue-type": "classic", "x-dead-letter-exchange": "cycle-exchange"},
                },
                {
                    "name": "unreachable-queue",
                    "vhost": "audit",
                    "durable": True,
                    "auto_delete": False,
                    "arguments": {"x-queue-type": "classic"},
                },
            ],
            "exchanges": [
                {
                    "name": "cycle-exchange",
                    "vhost": "audit",
                    "type": "direct",
                    "durable": True,
                    "auto_delete": False,
                    "internal": False,
                    "arguments": {},
                },
                {
                    "name": "discard",
                    "vhost": "audit",
                    "type": "fanout",
                    "durable": True,
                    "auto_delete": False,
                    "internal": False,
                    "arguments": {},
                },
                {
                    "name": "internal-unreachable",
                    "vhost": "audit",
                    "type": "direct",
                    "durable": True,
                    "auto_delete": False,
                    "internal": True,
                    "arguments": {},
                },
            ],
            "bindings": [
                {
                    "source": "cycle-exchange",
                    "vhost": "audit",
                    "destination": "cycle-queue",
                    "destination_type": "queue",
                    "routing_key": "",
                    "arguments": {},
                }
            ],
        }
    )


def _definitions_with_an_extra_structural_cycle() -> ClusterDefinitionsResponse:
    data = _definitions().model_dump(by_alias=True)
    data["bindings"].append(
        {
            "source": "cycle-exchange",
            "vhost": "audit",
            "destination": "cycle-exchange",
            "destination_type": "exchange",
            "routing_key": "self",
            "arguments": {},
        }
    )
    return ClusterDefinitionsResponse.model_validate(data)


def test_audit_returns_all_findings_from_the_public_facade() -> None:
    auditor = ClusterAuditor(_definitions())

    report = auditor.audit()

    assert isinstance(report, TopologyAuditReport)
    assert isinstance(report.structural_cycles, CycleSearchResult)
    assert report.structural_cycles == auditor.structural_cycles()
    assert report.structural_cycles.truncated is False
    assert report.message_loop_candidates.cycles == report.structural_cycles.cycles
    assert report.message_loop_candidates.truncated is False
    assert [exchange.id.name for exchange in report.black_hole_exchanges] == ["discard", "internal-unreachable"]
    assert [exchange.id.name for exchange in report.unreachable_internal_exchanges] == ["internal-unreachable"]
    assert [queue.id.name for queue in report.queues_without_declared_ingress] == ["unreachable-queue"]
    assert report.cross_vhost_shovels == ()
    assert report.shovels_with_unconfirmed_endpoints == ()
    assert report.shovels_with_unresolved_vhost == ()
    assert report.dangling_edges == ()


def test_audit_reuses_a_complete_structural_cycle_enumeration() -> None:
    auditor = ClusterAuditor(_definitions_with_an_extra_structural_cycle())

    with patch("rabbitmq_management_sdk.topology.cycles._johnson", wraps=_johnson) as johnson:
        report = auditor.audit()

    assert johnson.call_count == 1
    assert len(report.structural_cycles.cycles) == 2
    assert len(report.message_loop_candidates.cycles) == 1
    assert any(
        report.message_loop_candidates.cycles[0] is structural_cycle
        for structural_cycle in report.structural_cycles.cycles
    )


def test_audit_searches_candidates_separately_when_structural_cycles_are_truncated() -> None:
    auditor = ClusterAuditor(_definitions_with_an_extra_structural_cycle())

    with patch("rabbitmq_management_sdk.topology.cycles._johnson", wraps=_johnson) as johnson:
        report = auditor.audit(max_cycles=1)

    assert johnson.call_count == 2
    assert len(report.structural_cycles.cycles) == 1
    assert report.structural_cycles.truncated is True
    assert len(report.message_loop_candidates.cycles) == 1
    assert report.message_loop_candidates.truncated is False
    assert {edge.kind for edge in report.message_loop_candidates.cycles[0].edges} == {
        EdgeKind.BINDING,
        EdgeKind.DEAD_LETTER,
    }


def test_cluster_identity_metadata_is_available_from_the_facade() -> None:
    definitions = _definitions().model_copy(update={"original_cluster_name": "name-recorded-by-the-export"})
    auditor = ClusterAuditor(definitions, cluster_label="production-eu-west-1")

    assert isinstance(auditor.topology, ClusterTopology)
    assert auditor.cluster_id is None
    assert auditor.cluster_name == "name-recorded-by-the-export"
    assert auditor.cluster_label == "production-eu-west-1"
    assert auditor.topology.cluster_name == auditor.cluster_name
    assert auditor.topology.cluster_label == auditor.cluster_label


def test_facade_exposes_complete_and_cyclic_scc_views() -> None:
    auditor = ClusterAuditor(_definitions())

    components = auditor.strongly_connected_components()
    cyclic_components = auditor.cyclic_components()

    assert all(isinstance(component, StronglyConnectedComponent) for component in components)
    assert frozenset(node for component in components for node in component.nodes) == auditor.topology.all_node_ids
    assert cyclic_components == tuple(component for component in components if component.is_cyclic)
    assert len(cyclic_components) == 1
    assert {node.name for node in cyclic_components[0].nodes} == {"cycle-exchange", "cycle-queue"}


def test_exchange_observations_supply_referenced_system_exchanges() -> None:
    definitions = ClusterDefinitionsResponse.model_validate(
        {
            "vhosts": [{"name": "audit", "metadata": {"default_queue_type": "classic"}}],
            "queues": [
                {
                    "name": "events",
                    "vhost": "audit",
                    "durable": True,
                    "auto_delete": False,
                    "arguments": {"x-queue-type": "classic"},
                }
            ],
            "bindings": [
                {
                    "source": "amq.rabbitmq.trace",
                    "vhost": "audit",
                    "destination": "events",
                    "destination_type": "queue",
                    "routing_key": "#",
                    "arguments": {},
                }
            ],
        }
    )
    observed_exchange = ExchangeResponse.model_validate(
        {
            "name": "amq.rabbitmq.trace",
            "vhost": "audit",
            "type": "topic",
            "durable": True,
            "auto_delete": False,
            "internal": True,
            "arguments": {},
        }
    )

    auditor = ClusterAuditor(definitions, queues=[], exchanges=[observed_exchange])

    assert auditor.dangling_edges() == ()


def test_from_files_wraps_invalid_definitions_json_in_a_topology_load_error(tmp_path: Path) -> None:
    dump_path = tmp_path / "invalid.json"
    dump_path.write_text("{ definitely not JSON", encoding="utf-8")

    with pytest.raises(TopologyLoadError) as error:
        ClusterAuditor.from_files(dump_path)

    assert isinstance(error.value, RabbitMQError)
    assert error.value.__cause__ is not None


def test_from_files_wraps_definitions_schema_errors_in_a_topology_definitions_error(tmp_path: Path) -> None:
    dump_path = tmp_path / "invalid-schema.json"
    dump_path.write_text('{"queues": [{}]}', encoding="utf-8")

    with pytest.raises(TopologyDefinitionsError) as error:
        ClusterAuditor.from_files(dump_path)

    assert isinstance(error.value, RabbitMQError)
    assert error.value.__cause__ is not None


def test_in_memory_resource_observations_must_be_an_all_or_nothing_pair() -> None:
    with pytest.raises(TopologyAnalysisError, match="queues and exchanges"):
        ClusterAuditor(_definitions(), queues=[])


def test_from_files_resource_paths_must_be_an_all_or_nothing_pair(tmp_path: Path) -> None:
    queues_path = tmp_path / "queues.json"
    queues_path.write_text("[]", encoding="utf-8")

    with pytest.raises(TopologyAnalysisError, match="queues_path and exchanges_path"):
        ClusterAuditor.from_files(tmp_path / "definitions.json", queues_path=queues_path)


def test_from_files_wraps_invalid_resource_snapshot_schema(tmp_path: Path) -> None:
    definitions_path = tmp_path / "definitions.json"
    queues_path = tmp_path / "queues.json"
    exchanges_path = tmp_path / "exchanges.json"
    definitions_path.write_text(_definitions().model_dump_json(by_alias=True), encoding="utf-8")
    queues_path.write_text('{"items": []}', encoding="utf-8")
    exchanges_path.write_text("[]", encoding="utf-8")

    with pytest.raises(TopologyResourceSnapshotError, match="must be a JSON array") as error:
        ClusterAuditor.from_files(
            definitions_path,
            queues_path=queues_path,
            exchanges_path=exchanges_path,
        )

    assert isinstance(error.value, RabbitMQError)
