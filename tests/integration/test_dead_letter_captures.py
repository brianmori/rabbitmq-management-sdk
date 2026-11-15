"""Check effective DLX coverage against saved broker-selected policy evidence."""

from pathlib import Path

import pytest

from rabbitmq_management_sdk import ClusterAuditor
from rabbitmq_management_sdk.topology import DeadLetterCoverageStatus

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
        ("policy", "orders.q", "dlx.b", DeadLetterCoverageStatus.NO_DESTINATION),
        ("src", "fan.q", "my-fan.dlx", DeadLetterCoverageStatus.DESTINATION_POSSIBLE),
    ]
    assert [(node.vhost, node.name) for node in configured[-1].destinations] == [("src", "fan.q")]
    assert configured[-1].routing_key == "asd"
    assert auditor.audit().dead_letter_coverage == results
