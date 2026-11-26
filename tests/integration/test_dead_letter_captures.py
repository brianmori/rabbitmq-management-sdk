"""Check effective DLX coverage against saved broker observations."""

import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from rabbitmq_management_sdk import ClusterAuditor
from rabbitmq_management_sdk.resources.v4.admin.schemas.export_response import ClusterDefinitionsResponse
from rabbitmq_management_sdk.resources.v4.exchanges.schemas.exchange_response import ExchangeResponse
from rabbitmq_management_sdk.resources.v4.queues.schemas.queue_response import QueueResponse
from rabbitmq_management_sdk.topology import DeadLetterCoverageStatus, DeadLetterRoutingIssue, DeadLetterUncertainty

pytestmark = pytest.mark.integration
_CAPTURE = Path(__file__).parent / "rmq-4.2"


@pytest.mark.parametrize("suffix", ["", "-no-stats"])
def test_selected_policy_and_unbound_dlx_remain_distinct_from_absent_dlx(suffix: str) -> None:
    auditor = ClusterAuditor.from_files(
        _CAPTURE / "full-export-definitions.json",
        queues_path=_CAPTURE / f"queues-get-all{suffix}.json",
        exchanges_path=_CAPTURE / f"exchanges-get-all{suffix}.json",
    )

    results = auditor.dead_letter_coverage()
    configured = [result for result in results if result.dead_letter_exchange is not None]

    assert len(results) == 13
    assert [
        (result.queue.id.vhost, result.queue.id.name, result.dead_letter_exchange.name, result.status)
        for result in configured
        if result.dead_letter_exchange is not None
    ] == [
        ("dest", "test.q", "test.dlx", DeadLetterCoverageStatus.NO_DESTINATION),
        ("policy", "orders.q", "dlx.b", DeadLetterCoverageStatus.UNDETERMINED),
        ("src", "fan.q", "my-fan.dlx", DeadLetterCoverageStatus.DESTINATION_POSSIBLE),
    ]
    policy_dlx = configured[1].dead_letter_exchange
    assert policy_dlx is not None
    assert configured[1].issues == (
        DeadLetterRoutingIssue(policy_dlx, DeadLetterUncertainty.POLICY_SELECTION_NOT_CAPTURED),
    )
    assert [(node.vhost, node.name) for node in configured[-1].destinations] == [("src", "fan.q")]
    assert configured[-1].routing_key == "asd"
    assert auditor.audit().dead_letter_coverage == results


@pytest.mark.parametrize("suffix", ["", "-no-stats"])
def test_observed_arguments_recover_direct_dlx_from_incomplete_definitions(suffix: str) -> None:
    definitions = json.loads((_CAPTURE / "full-export-definitions.json").read_bytes())
    queues = TypeAdapter(list[QueueResponse]).validate_json((_CAPTURE / f"queues-get-all{suffix}.json").read_bytes())
    exchanges = TypeAdapter(list[ExchangeResponse]).validate_json(
        (_CAPTURE / f"exchanges-get-all{suffix}.json").read_bytes()
    )
    original = ClusterAuditor(
        ClusterDefinitionsResponse.model_validate(definitions), queues=queues, exchanges=exchanges
    )

    definition_queue = next(
        queue for queue in definitions["queues"] if (queue["vhost"], queue["name"]) == ("src", "fan.q")
    )
    del definition_queue["arguments"]
    recovered = ClusterAuditor(
        ClusterDefinitionsResponse.model_validate(definitions), queues=queues, exchanges=exchanges
    )

    assert recovered.topology.edges == original.topology.edges
    coverage = next(
        result
        for result in recovered.dead_letter_coverage()
        if (result.queue.id.vhost, result.queue.id.name) == ("src", "fan.q")
    )
    assert coverage.dead_letter_exchange is not None
    assert coverage.dead_letter_exchange.name == "my-fan.dlx"
    assert coverage.routing_key == "asd"
    assert coverage.status == DeadLetterCoverageStatus.DESTINATION_POSSIBLE
    assert coverage.destinations == (coverage.queue.id,)
