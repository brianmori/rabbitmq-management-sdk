"""Check contextual exchange coverage against captured RabbitMQ resources."""

import json
from pathlib import Path

import pytest

from rabbitmq_management_sdk import ClusterAuditor
from rabbitmq_management_sdk.topology import EdgeKind, RoutingIssue, RoutingStatus, RoutingUncertainty

pytestmark = pytest.mark.integration
_CAPTURE = Path(__file__).parent / "rmq-4.2"


@pytest.mark.parametrize("suffix", ["", "-no-stats"])
def test_exchange_chain_dead_ends_and_contextual_topic_destinations(suffix: str) -> None:
    auditor = ClusterAuditor.from_files(
        _CAPTURE / "full-export-definitions.json",
        queues_path=_CAPTURE / f"queues-get-all{suffix}.json",
        exchanges_path=_CAPTURE / f"exchanges-get-all{suffix}.json",
    )
    results = auditor.exchange_routing_coverage()
    groups = {(result.exchange.id.vhost, result.exchange.id.name): result for result in results}

    assert len(results) == len(auditor.topology.exchanges) == 19
    assert groups["src", "another.dx"].status == RoutingStatus.NO_DESTINATION
    assert groups["policy", "events"].status == RoutingStatus.UNDETERMINED
    assert groups["policy", "events"].issues == (
        RoutingIssue(groups["policy", "ae.b"].exchange.id, RoutingUncertainty.POLICY_SELECTION_NOT_CAPTURED),
    )
    assert groups["src", "src.tx"].status == RoutingStatus.UNDETERMINED
    assert groups["src", "x-local-random.rx"].status == RoutingStatus.UNDETERMINED
    assert groups["src", "x-local-random.rx"].issues[0].reason == RoutingUncertainty.UNSUPPORTED_ROUTING_SEMANTICS
    assert groups["src", "my-fan.dlx"].status == RoutingStatus.DESTINATION_POSSIBLE
    assert [node.name for node in groups["src", "my-fan.dlx"].destinations] == ["fan.q"]
    old_findings = {exchange.id for exchange in auditor.black_hole_exchanges()}
    assert groups["src", "another.dx"].exchange.id not in old_findings
    assert groups["policy", "events"].exchange.id not in old_findings

    keyed = {
        (result.exchange.id.vhost, result.exchange.id.name): result
        for result in auditor.exchange_routing_coverage(routing_key="holidays.ita.sicily.weather")
    }
    assert keyed["src", "src.tx"].status == RoutingStatus.DESTINATION_POSSIBLE
    assert [node.name for node in keyed["src", "src.tx"].destinations] == ["holidays.ita.sicily.q"]
    assert auditor.audit().exchange_routing_coverage == results


@pytest.mark.parametrize("observed_arguments_captured", [True, False])
def test_alternate_arguments_omitted_from_captured_definitions(
    tmp_path: Path, observed_arguments_captured: bool
) -> None:
    capture = Path(__file__).parent / "rmq-4.3" / "complex-topology"
    definitions = json.loads((capture / "full-export-definitions.json").read_bytes())
    exchanges = json.loads((capture / "exchanges.json").read_bytes())
    exchange_id = ("topology-demo.policies", "policy.override.exchange")
    definition = next(row for row in definitions["exchanges"] if (row["vhost"], row["name"]) == exchange_id)
    del definition["arguments"]
    if not observed_arguments_captured:
        observation = next(row for row in exchanges if (row["vhost"], row["name"]) == exchange_id)
        del observation["arguments"]

    definitions_path = tmp_path / "definitions.json"
    definitions_path.write_text(json.dumps(definitions), encoding="utf-8")
    exchanges_path = tmp_path / "exchanges.json"
    exchanges_path.write_text(json.dumps(exchanges), encoding="utf-8")
    auditor = ClusterAuditor.from_files(
        definitions_path, queues_path=capture / "queues.json", exchanges_path=exchanges_path
    )

    coverage = next(
        result
        for result in auditor.exchange_routing_coverage(routing_key="route")
        if (result.exchange.id.vhost, result.exchange.id.name) == exchange_id
    )
    alternates = {
        edge.target.name
        for edge in auditor.topology.edges
        if edge.source == coverage.exchange.id and edge.kind == EdgeKind.ALTERNATE_EXCHANGE
    }
    if observed_arguments_captured:
        assert alternates == {"argument.fallback"}
        assert coverage.status == RoutingStatus.DESTINATION_POSSIBLE
        assert [node.name for node in coverage.destinations] == ["argument.fallback.q"]
        assert not coverage.issues
    else:
        assert not alternates
        assert coverage.status == RoutingStatus.UNDETERMINED
        assert not coverage.destinations
        assert coverage.issues == (
            RoutingIssue(coverage.exchange.id, RoutingUncertainty.EXCHANGE_ARGUMENTS_NOT_CAPTURED),
        )
