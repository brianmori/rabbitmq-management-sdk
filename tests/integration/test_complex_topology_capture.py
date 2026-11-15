"""Exercise topology features against one complete, broker-generated demo."""

import hashlib
import json
import random
from collections.abc import Callable
from pathlib import Path

import pytest
from tests.shared.complex_topology import CAPTURE, CHECKS, DEMO_PREFIX

from rabbitmq_management_sdk import ClusterAuditor

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def complex_auditor() -> ClusterAuditor:
    return ClusterAuditor.from_files(
        CAPTURE / "full-export-definitions.json",
        queues_path=CAPTURE / "queues.json",
        exchanges_path=CAPTURE / "exchanges.json",
    )


@pytest.mark.parametrize("check", CHECKS, ids=lambda check: check.__name__)
def test_complex_capture(complex_auditor: ClusterAuditor, check: Callable[[ClusterAuditor, str], None]) -> None:
    check(complex_auditor, DEMO_PREFIX)


def test_explicit_alias_evidence_confirms_only_the_known_endpoint() -> None:
    auditor = ClusterAuditor.from_files(
        CAPTURE / "full-export-definitions.json",
        queues_path=CAPTURE / "queues.json",
        exchanges_path=CAPTURE / "exchanges.json",
        in_cluster_amqp_hosts={"127.0.0.1"},
    )
    assert {shovel.id.name for shovel in auditor.shovels_with_unconfirmed_endpoints()} == {"uncertain.failover"}
    alias = next(shovel for shovel in auditor.topology.shovels if shovel.id.name == "alias.unconfirmed")
    assert any(edge.target == alias.id for edge in auditor.topology.edges)


def test_capture_provenance_and_complete_export_are_preserved() -> None:
    provenance = json.loads((CAPTURE / "provenance.json").read_bytes())
    for filename, digest in provenance["sha256"].items():
        assert hashlib.sha256((CAPTURE / filename).read_bytes()).hexdigest() == digest
    definitions = json.loads((CAPTURE / "full-export-definitions.json").read_bytes())
    assert definitions["rabbitmq_version"] == provenance["broker_version"] == "4.3.6"
    assert {
        "users",
        "permissions",
        "vhosts",
        "queues",
        "exchanges",
        "bindings",
        "parameters",
        "policies",
    } <= definitions.keys()
    assert len(definitions["vhosts"]) == 11  # ten demo vhosts and the broker's default vhost
    assert [user["name"] for user in definitions["users"]] == ["guest"]


@pytest.mark.parametrize("seed", [0, 43, 87])
def test_capture_order_does_not_change_graph_or_report(
    complex_auditor: ClusterAuditor, tmp_path: Path, seed: int
) -> None:
    rng = random.Random(seed)
    filenames = ("full-export-definitions.json", "queues.json", "exchanges.json")
    for filename in filenames:
        payload = json.loads((CAPTURE / filename).read_bytes())
        arrays = (
            [value for value in payload.values() if isinstance(value, list)] if isinstance(payload, dict) else [payload]
        )
        for records in arrays:
            rng.shuffle(records)
        (tmp_path / filename).write_text(json.dumps(payload), encoding="utf-8")
    reordered = ClusterAuditor.from_files(
        tmp_path / filenames[0],
        queues_path=tmp_path / filenames[1],
        exchanges_path=tmp_path / filenames[2],
    )
    assert reordered.topology == complex_auditor.topology
    assert reordered.audit() == complex_auditor.audit()
