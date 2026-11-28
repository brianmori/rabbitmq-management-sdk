"""Replay sanitized RabbitMQ 4.2 and 4.3 policy captures."""

import json
from pathlib import Path

import pytest

from rabbitmq_management_sdk import ClusterAuditor
from rabbitmq_management_sdk.topology import (
    ConfigurationSource,
    ConfigurationStatus,
    ConfigurationUncertainty,
    DeadLetterCoverageStatus,
    StreamRetentionStatus,
)

pytestmark = pytest.mark.integration
_CAPTURES = Path(__file__).parent / "queue-limits"


@pytest.mark.parametrize("version", ["4.2.9", "4.3.6"])
def test_queue_limits_from_sanitized_capture(version: str, tmp_path: Path) -> None:
    capture = json.loads((_CAPTURES / f"rmq-{version}.json").read_text(encoding="utf-8"))
    for section in ("definitions", "queues", "exchanges"):
        (tmp_path / f"{section}.json").write_text(json.dumps(capture[section]), encoding="utf-8")
    auditor = ClusterAuditor.from_files(
        tmp_path / "definitions.json",
        queues_path=tmp_path / "queues.json",
        exchanges_path=tmp_path / "exchanges.json",
    )

    history, orders, work = auditor.queue_limits()
    assert [row.queue.name for row in (history, orders, work)] == ["history", "orders", "work"]
    assert all(row.configuration.broker_version == version for row in (history, orders, work))
    assert orders.configuration.max_length.value == 2
    assert orders.configuration.message_ttl_ms.value == 60_000
    assert orders.configuration.queue_expiry_ms.status == ConfigurationStatus.UNSET
    assert orders.configuration.overflow.value == "drop-head"
    assert work.configuration.overflow == auditor.quorum_delivery_configuration()[0].configuration.overflow
    assert work.configuration.overflow.value == "drop-head"
    assert work.configuration.queue_expiry_ms.value == 600_000
    assert work.dead_letter_coverage.status == DeadLetterCoverageStatus.NO_DESTINATION
    assert history.configuration.stream_max_age_ms.value == 7_200_000
    assert history.configuration.stream_max_length_bytes.value == 2_000_000
    assert history.configuration.max_length_bytes.status == ConfigurationStatus.NOT_APPLICABLE
    assert {
        item.policy_name for item in history.configuration.stream_max_length_bytes.evidence if item.policy_name
    } == {
        "history-policies",
        "history-operator-policies",
    }
    assert ConfigurationSource.EFFECTIVE_POLICY in {
        item.source for item in history.configuration.stream_max_length_bytes.evidence
    }
    (retention,) = auditor.stream_retention()
    assert retention.queue == history.queue
    assert retention.status == StreamRetentionStatus.CONFIGURED
    assert retention.stream_max_age_ms == history.configuration.stream_max_age_ms
    assert retention.stream_max_length_bytes == history.configuration.stream_max_length_bytes
    segment_size = retention.stream_max_segment_size_bytes
    assert segment_size.status == ConfigurationStatus.UNKNOWN
    assert segment_size.evidence == ()
    assert segment_size.uncertainties == (ConfigurationUncertainty.STREAM_CREATION_CONFIGURATION_NOT_CAPTURED,)
    report = auditor.audit()
    assert report.queue_limits == (history, orders, work)
    assert report.stream_retention == (retention,)
