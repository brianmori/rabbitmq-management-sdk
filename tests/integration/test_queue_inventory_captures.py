"""Validate inventory from saved RabbitMQ definitions and API observations."""

from pathlib import Path

import pytest

from rabbitmq_management_sdk import ClusterAuditor

pytestmark = pytest.mark.integration
_CAPTURES = Path(__file__).parent / "rmq-4.2"


@pytest.mark.parametrize("suffix", ["", "-no-stats"])
def test_inventory_retains_empty_vhosts_and_defaults_from_capture(suffix: str) -> None:
    auditor = ClusterAuditor.from_files(
        _CAPTURES / "full-export-definitions.json",
        queues_path=_CAPTURES / f"queues-get-all{suffix}.json",
        exchanges_path=_CAPTURES / f"exchanges-get-all{suffix}.json",
    )

    inventory = auditor.queue_type_inventory()

    assert inventory.queue_count == 13
    assert [(group.queue_type, group.queue_count) for group in inventory.queue_types] == [
        ("classic", 3),
        ("quorum", 10),
    ]
    assert [(vhost.vhost, vhost.default_queue_type, vhost.queue_count) for vhost in inventory.vhosts] == [
        ("/", "classic", 2),
        ("dest", "classic", 2),
        ("policy", "quorum", 1),
        ("src", "quorum", 5),
        ("test-dst", "classic", 1),
        ("test-src", "classic", 2),
        ("test-vh-export", "classic", 0),
        ("test-vhost", "classic", 0),
    ]
    assert auditor.audit(max_cycles=1).queue_type_inventory == inventory
