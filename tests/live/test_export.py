"""A full export retains a vhost created through the SDK."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from rabbitmq_management_sdk import AuditCapture, QueueRequest, QuorumQueueRequest

if TYPE_CHECKING:
    from pathlib import Path

    from rabbitmq_management_sdk import RabbitMQClient


@pytest.mark.live
def test_global_export(rabbitmq_client_compatibility: RabbitMQClient, live_vhost: str) -> None:
    definitions = rabbitmq_client_compatibility.admin.export_definitions()
    assert live_vhost in {vhost.name for vhost in definitions.vhosts}


@pytest.mark.live
def test_collect_and_replay_cluster_audit(live_client: RabbitMQClient, live_vhost: str, tmp_path: Path) -> None:
    for name in ("work/one", "work%two"):
        live_client.queues.create(name, QueueRequest(arguments=QuorumQueueRequest(delivery_limit=-1)))

    capture = live_client.collect_audit_capture(page_size=1)
    assert len(capture.manifest.queues.pages) >= 2
    assert len(capture.manifest.exchanges.pages) >= 2
    assert capture.manifest.broker_version == str(live_client.version)

    path = tmp_path / "capture.json"
    capture.save(path)
    auditor = AuditCapture.load(path).to_auditor()
    configs = {
        item.queue.name: item.configuration
        for item in auditor.quorum_delivery_configuration()
        if item.queue.vhost == live_vhost
    }
    assert set(configs) == {"work/one", "work%two"}
    assert all(config.delivery_limit.value == -1 for config in configs.values())
    assert auditor.quorum_resilience() == capture.to_auditor().quorum_resilience()
