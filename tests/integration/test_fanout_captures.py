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
    assert auditor.audit(max_cycles=1).fanout_exchange_bindings == groups
