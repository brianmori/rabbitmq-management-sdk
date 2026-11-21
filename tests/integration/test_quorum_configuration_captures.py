"""Replay sanitized 4.3 configuration through the public manager boundaries."""

from __future__ import annotations

import json
from pathlib import Path

import httpx2
import pytest

from rabbitmq_management_sdk import ClusterAuditor, DelayedRetryType
from rabbitmq_management_sdk.http_adapter.httpx2 import Httpx2Adapter
from rabbitmq_management_sdk.resources.v4.admin.services import AdminManager
from rabbitmq_management_sdk.resources.v4.policies.services import PolicyManager
from rabbitmq_management_sdk.resources.v4.queues.schemas.queue_response import QuorumQueueResponse
from rabbitmq_management_sdk.resources.v4.queues.services import QueueManager
from rabbitmq_management_sdk.topology import DeadLetterCoverageStatus, NodeId, NodeKind, PolicyId, PolicyKind

pytestmark = pytest.mark.integration
_CAPTURE = Path(__file__).parent / "rmq-4.3" / "quorum-configuration" / "configuration.json"


def test_quorum_configuration_capture_through_queue_policy_and_definitions_managers() -> None:
    capture = json.loads(_CAPTURE.read_text(encoding="utf-8"))
    assert capture["broker_version"] == "4.3.6"
    calls: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "GET"
        calls.append(request.url.path)
        responses = {
            "/api/queues/captured/configured": capture["queue"],
            "/api/policies/captured/retry": capture["policy"],
            "/api/definitions": capture["definitions"],
        }
        return httpx2.Response(200, json=responses[request.url.path])

    with Httpx2Adapter(host="localhost", port=15672, transport=httpx2.MockTransport(handler)) as adapter:
        queue = QueueManager(adapter, "captured", False).get("configured")
        policy = PolicyManager(adapter, "captured", False).get("retry")
        definitions = AdminManager(adapter, False).export_definitions()

    assert calls == ["/api/queues/captured/configured", "/api/policies/captured/retry", "/api/definitions"]
    assert isinstance(queue.arguments, QuorumQueueResponse)
    assert queue.arguments.delayed_retry_type == DelayedRetryType.ALL
    assert queue.arguments.delayed_retry_min == 1000
    assert queue.arguments.delayed_retry_max == 30_000
    assert queue.arguments.consumer_timeout == 120_000
    assert queue.arguments.consumer_disconnected_timeout == 60_000
    assert queue.arguments.model_dump(by_alias=True, exclude_none=True) == capture["queue"]["arguments"]

    assert policy.definition.delayed_retry_type == DelayedRetryType.ALL
    assert policy.definition.delayed_retry_min == 2000
    assert policy.definition.delayed_retry_max == 40_000
    assert policy.definition.consumer_timeout == 180_000
    assert policy.definition.consumer_disconnected_timeout == 90_000
    assert policy.definition.model_dump() == capture["policy"]["definition"]

    exported = next(queue for queue in definitions.queues if queue.name == "configured")
    assert exported.arguments.delayed_retry_type == DelayedRetryType.ALL
    assert exported.arguments.delayed_retry_min == 1000
    assert exported.arguments.delayed_retry_max == 30_000
    assert exported.arguments.consumer_timeout == 120_000
    assert exported.arguments.consumer_disconnected_timeout == 60_000
    for queue_definition, wire in zip(definitions.queues, capture["definitions"]["queues"], strict=True):
        assert queue_definition.arguments.model_dump() == wire["arguments"]
    assert definitions.policies[0].definition.model_dump() == capture["policy"]["definition"]


def test_captured_quorum_configuration_loads_for_audit_without_inventing_routes(tmp_path: Path) -> None:
    capture = json.loads(_CAPTURE.read_text(encoding="utf-8"))
    path = tmp_path / "definitions.json"
    path.write_text(json.dumps(capture["definitions"]), encoding="utf-8")
    # Retry-only policies need no routing-policy observations. This projection
    # contains no bindings or DLXs, so no consumer return should become a route.
    auditor = ClusterAuditor.from_files(path)
    baseline = json.loads(path.read_text(encoding="utf-8"))
    for queue in baseline["queues"]:
        queue["arguments"] = {"x-queue-type": "quorum"}
    baseline["policies"] = []
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    reference = ClusterAuditor.from_files(baseline_path)

    assert auditor.topology.all_node_ids == reference.topology.all_node_ids
    assert auditor.topology.edges == reference.topology.edges
    assert auditor.structural_cycles() == reference.structural_cycles()
    assert auditor.message_loop_candidates() == reference.message_loop_candidates()
    assert not auditor.topology.edges
    assert not auditor.structural_cycles().cycles
    assert not auditor.message_loop_candidates().cycles
    assert {queue.id.name for queue in auditor.topology.queues} == {"configured", "policy-queue"}
    assert all(result.status == DeadLetterCoverageStatus.NO_DLX for result in auditor.dead_letter_coverage())
    assert [queue.arguments.model_dump() for queue in auditor.definitions.queues] == [
        queue["arguments"] for queue in capture["definitions"]["queues"]
    ]
    assert auditor.definitions.policies[0].definition.model_dump() == capture["policy"]["definition"]


@pytest.mark.parametrize("filename", ["configuration.json", "operator-configuration.json"])
def test_captured_policy_observations_resolve_quorum_delivery_audit(tmp_path: Path, filename: str) -> None:
    capture = json.loads((_CAPTURE.parent / filename).read_text(encoding="utf-8"))
    for section in ("definitions", "queues", "exchanges"):
        (tmp_path / f"{section}.json").write_text(json.dumps(capture[section]), encoding="utf-8")
    auditor = ClusterAuditor.from_files(
        tmp_path / "definitions.json",
        queues_path=tmp_path / "queues.json",
        exchanges_path=tmp_path / "exchanges.json",
    )
    results = {item.queue.name: item.configuration for item in auditor.quorum_delivery_configuration()}
    configured = results["configured"]
    assert configured.broker_version == "4.3.6"
    assert configured.consumer_timeout.value == 120_000
    assert configured.delayed_retry_min.value == 1000
    assert configured.delayed_retry_type.value == ("returned" if filename.startswith("operator") else "all")
    assert configured.delayed_retry_max.value == (8000 if filename.startswith("operator") else 30_000)
    assert configured.delivery_limit.value == (25 if filename.startswith("operator") else 20)
    if filename.startswith("operator"):
        assert {item.policy_name for item in configured.delayed_retry_min.evidence if item.policy_name} == {
            "retry",
            "operator",
        }
        resource = NodeId("captured", "configured", NodeKind.QUEUE, auditor.topology.cluster_id)
        operator = auditor.policy_lookup(PolicyId("captured", "operator", PolicyKind.OPERATOR, resource.cluster_id))
        regular = auditor.policy_lookup(PolicyId("captured", "retry", PolicyKind.REGULAR, resource.cluster_id))
        assert operator is not None
        assert regular is not None
        assert operator.selected_resources == regular.selected_resources == (resource,)
        assert operator.declaration.apply_to == "quorum_queues"
        assert NodeId("captured", "amq.direct", NodeKind.EXCHANGE, resource.cluster_id) in {
            row.resource for row in auditor.policy_inventory().missing_selections if row.kind is PolicyKind.OPERATOR
        }
    else:
        assert results["policy-queue"].delayed_retry_min.value == 2000
    assert not auditor.structural_cycles().cycles
    assert not auditor.message_loop_candidates().cycles
    assert auditor.audit().quorum_delivery_configuration == auditor.quorum_delivery_configuration()


def test_rabbitmq_42_operator_parameters_are_inventory_declarations(tmp_path: Path) -> None:
    original = _CAPTURE.parents[2] / "rmq-4.2" / "full-export-definitions.json"
    capture = json.loads(original.read_text(encoding="utf-8"))
    path = tmp_path / "declarations.json"
    parameters = [
        parameter
        for parameter in capture["parameters"]
        if parameter["component"] == "operator_policy" and parameter["vhost"] == "policy"
    ]
    path.write_text(
        json.dumps({"rabbitmq_version": capture["rabbitmq_version"], "parameters": parameters}), encoding="utf-8"
    )
    auditor = ClusterAuditor.from_files(path)

    assert [row.declaration.id.name for row in auditor.policy_inventory().policies] == [
        "all-queues",
        "classic-queues",
        "quorum.q",
        "quorum.s",
        "stream.queues",
    ]
    row = auditor.policy_lookup(PolicyId("policy", "quorum.s", PolicyKind.OPERATOR))
    assert row is not None
    assert (row.declaration.pattern, row.declaration.apply_to, row.declaration.priority) == (
        "quorum123.*",
        "quorum_queues",
        300,
    )
    assert row.selected_resources == ()
