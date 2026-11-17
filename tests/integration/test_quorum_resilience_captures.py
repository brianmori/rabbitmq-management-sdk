"""Replay quorum placement from RabbitMQ 4.2.8 and 4.3.6 captures."""

from pathlib import Path

import pytest

from rabbitmq_management_sdk import ClusterAuditor
from rabbitmq_management_sdk.topology import QuorumFailureScenario, QuorumPlacement

pytestmark = pytest.mark.integration
_CAPTURES = Path(__file__).parent


@pytest.mark.parametrize(
    ("directory", "queues", "exchanges", "broker", "queue_count"),
    [
        ("rmq-4.2", "queues-get-all.json", "exchanges-get-all.json", "rabbit@rabbitmq-sa", 10),
        ("rmq-4.2", "queues-get-all-no-stats.json", "exchanges-get-all-no-stats.json", "rabbit@rabbitmq-sa", 10),
        ("rmq-4.3/complex-topology", "queues.json", "exchanges.json", "rabbit@sdk-topology-demo-43", 3),
    ],
)
def test_quorum_placement_and_single_node_loss_from_captures(
    directory: str, queues: str, exchanges: str, broker: str, queue_count: int
) -> None:
    capture = _CAPTURES / directory
    auditor = ClusterAuditor.from_files(
        capture / "full-export-definitions.json",
        queues_path=capture / queues,
        exchanges_path=capture / exchanges,
    )
    scenario = QuorumFailureScenario(failed_domains=("demo-zone",))
    report = auditor.audit(failure_domains={broker: "demo-zone"}, scenarios=[scenario], max_cycles=1).quorum_resilience

    assert len(report.queues) == queue_count
    for queue in report.queues:
        assert queue.queue.quorum_placement == QuorumPlacement((broker,), broker, (broker,))
        assert queue.required_majority == 1
        assert queue.theoretical_failure_tolerance == queue.observed_failure_margin == 0
        assert queue.observed_online_majority is True
    (concentration,) = report.brokers
    assert concentration.broker == broker
    assert concentration.member_queue_count == concentration.leader_queue_count == queue_count
    (loss,) = report.scenarios
    assert loss.affected_queues == concentration.member_queues == concentration.leader_queues
    for impact in loss.queues:
        assert impact.leader_failed is True
        assert impact.member_majority_after_failure is impact.observed_online_majority_after_failure is False
        assert impact.observed_failure_margin_after_failure == -1
