"""Replay projected definitions for the vhost permission and limit inventory."""

from pathlib import Path

import pytest

from rabbitmq_management_sdk import ClusterAuditor
from rabbitmq_management_sdk.topology import VhostLimit, VhostLimitStatus, VhostPermission, VhostTopicPermission

pytestmark = pytest.mark.integration
_CAPTURES = Path(__file__).parent / "vhost-permissions"


def test_vhost_permissions_and_runtime_limits_from_rabbitmq_42_capture() -> None:
    auditor = ClusterAuditor.from_files(_CAPTURES / "rmq-4.2.8.json")

    inventory = auditor.vhost_permissions_inventory()
    limited, no_cap, topic = inventory.vhosts
    assert inventory.permissions_captured
    assert inventory.topic_permissions_captured
    assert (limited.vhost, no_cap.vhost, topic.vhost) == ("/limited", "/no-cap", "/topic")
    assert limited.permissions == no_cap.permissions == (VhostPermission("application", ".*", ".*", ".*"),)
    assert limited.max_queues == limited.max_connections == VhostLimit(VhostLimitStatus.CAPPED, 3)
    assert no_cap.max_queues == no_cap.max_connections == VhostLimit(VhostLimitStatus.UNSET)
    assert limited.topic_permissions == no_cap.topic_permissions == ()
    assert topic.topic_permissions == (
        VhostTopicPermission("application", "amq.fanout", ".*", ".*"),
        VhostTopicPermission("observer", "", ".*", ".*"),
    )
    assert topic.max_queues == VhostLimit(VhostLimitStatus.UNSET)
    assert topic.max_connections == VhostLimit(VhostLimitStatus.CAPPED, 60)
    assert auditor.audit().vhost_permissions_inventory == inventory


def test_vhost_permissions_and_empty_limit_mirror_from_rabbitmq_43_capture() -> None:
    auditor = ClusterAuditor.from_files(_CAPTURES / "rmq-4.3.6.json")

    inventory = auditor.vhost_permissions_inventory()
    (row,) = inventory.vhosts
    assert row.vhost == "/control"
    assert row.permissions == (VhostPermission("operator", ".*", ".*", ".*"),)
    assert row.topic_permissions == ()
    assert row.max_queues == row.max_connections == VhostLimit(VhostLimitStatus.UNSET)
