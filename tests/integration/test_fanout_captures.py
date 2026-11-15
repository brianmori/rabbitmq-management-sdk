"""Check fanout findings against sanitized RabbitMQ captures."""

from pathlib import Path

import pytest

from rabbitmq_management_sdk import ClusterAuditor

pytestmark = pytest.mark.integration
_CAPTURES = Path(__file__).parent / "rmq-4.2"


@pytest.mark.parametrize("suffix", ["", "-no-stats"])
def test_fanout_queue_replication_survives_capture_loading(suffix: str) -> None:
    auditor = ClusterAuditor.from_files(
        _CAPTURES / "full-export-definitions.json",
        queues_path=_CAPTURES / f"queues-get-all{suffix}.json",
        exchanges_path=_CAPTURES / f"exchanges-get-all{suffix}.json",
    )
    groups = auditor.fanout_exchange_bindings()

    assert [(group.exchange.id.vhost, group.exchange.id.name) for group in groups] == [
        ("dest", "dest.fx"),
        ("dest", "test.dlx"),
        ("src", "my-fan.dlx"),
        ("src", "src.fx"),
    ]
    (source,) = (group for group in groups if group.exchange.id.name == "my-fan.dlx")
    assert [(queue.id.vhost, queue.id.name, queue.queue_type, queue.replication_factor) for queue in source.queues] == [
        ("src", "fan.q", "quorum", 1),
    ]
    assert source.queues[0].durable is True
    assert (source.queue_count, source.durable_queue_count, source.quorum_queue_count) == (1, 1, 1)
    assert source.known_quorum_member_count == source.quorum_member_count == 1
    assert source.unknown_queue_type_count == source.unknown_durability_count == 0
    assert source.quorum_queues_without_membership_count == 0
    assert all(group.queue_count == group.quorum_member_count == 0 for group in groups if group is not source)
    assert auditor.audit(max_cycles=1).fanout_exchange_bindings == groups
