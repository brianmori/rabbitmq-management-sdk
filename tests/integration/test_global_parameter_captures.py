"""Global parameter nulls survive capture loading without changing topology."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from rabbitmq_management_sdk import ClusterAuditor, TopologyDefinitionsError

pytestmark = pytest.mark.integration
_CAPTURES = Path(__file__).parent / "rmq-4.2"


def test_from_files_preserves_explicit_null_global_parameter(tmp_path: Path) -> None:
    path = tmp_path / "definitions.json"
    path.write_text('{"vhosts": [], "global_parameters": [{"name": "review-marker", "value": null}]}', encoding="utf-8")

    auditor = ClusterAuditor.from_files(path)

    (parameter,) = auditor.definitions.global_parameters
    assert parameter.value is None
    assert parameter.model_dump() == {"name": "review-marker", "value": None}
    assert auditor.topology.nodes == frozenset()
    assert auditor.topology.edges == frozenset()
    assert auditor.topology.cluster_id is None


def test_from_files_wraps_a_missing_global_parameter_value(tmp_path: Path) -> None:
    path = tmp_path / "definitions.json"
    path.write_text('{"global_parameters": [{"name": "review-marker"}]}', encoding="utf-8")

    with pytest.raises(TopologyDefinitionsError) as error:
        ClusterAuditor.from_files(path)

    cause = error.value.__cause__
    assert isinstance(cause, ValidationError)
    assert cause.errors()[0]["loc"] == ("global_parameters", 0, "value")
    assert cause.errors()[0]["type"] == "missing"


@pytest.mark.parametrize("suffix", ["", "-no-stats"])
def test_null_global_parameter_does_not_change_captured_topology_or_audit(tmp_path: Path, suffix: str) -> None:
    queues_path = _CAPTURES / f"queues-get-all{suffix}.json"
    exchanges_path = _CAPTURES / f"exchanges-get-all{suffix}.json"
    definitions_path = _CAPTURES / "full-export-definitions.json"
    baseline = ClusterAuditor.from_files(definitions_path, queues_path=queues_path, exchanges_path=exchanges_path)
    payload = json.loads(definitions_path.read_text(encoding="utf-8"))
    payload["global_parameters"].append({"name": "review-marker", "value": None})
    modified_path = tmp_path / "definitions-with-null.json"
    modified_path.write_text(json.dumps(payload), encoding="utf-8")

    auditor = ClusterAuditor.from_files(modified_path, queues_path=queues_path, exchanges_path=exchanges_path)

    assert auditor.definitions.global_parameters[-1].value is None
    assert auditor.definitions.cluster_name == baseline.definitions.cluster_name
    assert auditor.definitions.internal_cluster_id == baseline.definitions.internal_cluster_id
    assert auditor.definitions.cluster_tags == baseline.definitions.cluster_tags
    assert auditor.topology == baseline.topology
    assert auditor.audit() == baseline.audit()
