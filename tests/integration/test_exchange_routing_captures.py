"""Check contextual exchange coverage against captured RabbitMQ resources."""

from pathlib import Path

import pytest

from rabbitmq_management_sdk import ClusterAuditor
from rabbitmq_management_sdk.topology import RoutingStatus, RoutingUncertainty

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
    assert groups["policy", "events"].status == RoutingStatus.NO_DESTINATION
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
