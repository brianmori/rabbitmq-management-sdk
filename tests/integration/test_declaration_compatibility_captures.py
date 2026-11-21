"""Replay binding and declaration evidence from disposable RabbitMQ captures."""

import json
from pathlib import Path

import pytest

from rabbitmq_management_sdk import ClusterAuditor
from rabbitmq_management_sdk.topology import BindingDurabilityKind, QueueDeclarationStatus

pytestmark = pytest.mark.integration
_CAPTURES = Path(__file__).parent / "declaration-compatibility"


@pytest.mark.parametrize(("version", "shared_accepted"), [("4.2.9", True), ("4.3.8", False)])
def test_declaration_capture(version: str, shared_accepted: bool, tmp_path: Path) -> None:
    capture = json.loads((_CAPTURES / f"rmq-{version}.json").read_text(encoding="utf-8"))
    for section in ("definitions", "queues", "exchanges"):
        (tmp_path / f"{section}.json").write_text(json.dumps(capture[section]), encoding="utf-8")
    auditor = ClusterAuditor.from_files(
        tmp_path / "definitions.json",
        queues_path=tmp_path / "queues.json",
        exchanges_path=tmp_path / "exchanges.json",
    )

    assert auditor.definitions.rabbitmq_version == version
    assert capture["observations"] == {
        "private_declaration": "accepted",
        "private_exported": False,
        "shared_declaration": "accepted" if shared_accepted else "denied_by_transient_nonexcl_rule",
    }
    assert "private" not in {queue.id.name for queue in auditor.topology.queues}
    results = {(item.source.name, item.target.name): item for item in auditor.binding_durability()}
    assert len(results) == (8 if shared_accepted else 6)
    transient = results["source.transient", "target.transient"]
    assert (transient.source_durable, transient.target_durable) == (False, False)
    assert transient.classification is BindingDurabilityKind.FULLY_TRANSIENT
    mixed = results["source.transient", "work"]
    assert (mixed.source_durable, mixed.target_durable) == (False, True)
    assert mixed.classification is BindingDurabilityKind.UNCLASSIFIED

    if shared_accepted:
        semi_durable = results["source.durable", "shared"]
        assert semi_durable.classification is BindingDurabilityKind.SEMI_DURABLE
        (finding,) = auditor.queue_declaration_compatibility()
        assert finding.queue == semi_durable.target
        assert finding.exclusive is False
        assert finding.status is QueueDeclarationStatus.REJECTED_BY_DEFAULT
        (without_observations,) = ClusterAuditor.from_files(
            tmp_path / "definitions.json"
        ).queue_declaration_compatibility()
        assert without_observations.queue == finding.queue
        assert without_observations.exclusive is None
        assert without_observations.status is QueueDeclarationStatus.INDETERMINATE
    else:
        assert auditor.queue_declaration_compatibility() == ()
