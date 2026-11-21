"""Verify the public offline inventory and its capture boundary."""

import pickle
from dataclasses import asdict

import pytest

from rabbitmq_management_sdk import ClusterAuditor, TopologyAnalysisError
from rabbitmq_management_sdk.resources.v4.admin.schemas.export_response import ClusterDefinitionsResponse
from rabbitmq_management_sdk.topology import (
    ClusterTopology,
    NodeId,
    NodeKind,
    QueueNode,
    VhostLimit,
    VhostLimitStatus,
    VhostPermission,
    VhostPermissions,
    VhostPermissionsInventory,
    VhostTopicPermission,
    VirtualHostMetadata,
)

pytestmark = pytest.mark.unit


def test_vhost_permissions_inventory_survives_graph_transfer_without_user_or_parameter_payloads() -> None:
    password_hash = "SYNTHETIC_PASSWORD_HASH_MUST_NOT_BE_RETAINED"
    payload_secret = "SYNTHETIC_UNRELATED_PARAMETER_MUST_NOT_BE_RETAINED"
    grant = {"user": "payments-api", "configure": "^$", "write": r"^payments\.in$", "read": ""}
    topic = {"user": "payments-api", "exchange": "events", "write": r"^paid\.", "read": r"^refund\."}
    definitions = ClusterDefinitionsResponse.model_validate(
        {
            "rabbitmq_version": "4.3.8",
            "vhosts": [{"name": "/payments"}, {"name": "/other"}, {"name": "/empty"}],
            "permissions": [
                {**grant, "vhost": "/payments"},
                {**grant, "vhost": "/other", "write": ".*"},
                {**grant, "vhost": "/payments", "user": "admin", "configure": "("},
            ],
            "topic_permissions": [
                {**topic, "vhost": "/payments"},
                {**topic, "vhost": "/other", "exchange": ""},
                {**topic, "vhost": "/payments", "exchange": "archived"},
            ],
            "users": [
                {
                    "name": "payments-api",
                    "password_hash": password_hash,
                    "hashing_algorithm": "rabbit_password_hashing_sha256",
                    "tags": ["administrator"],
                    "limits": {"max-connections": 1},
                },
            ],
            "parameters": [
                {
                    "component": "vhost-limits",
                    "name": "limits",
                    "vhost": "/payments",
                    "value": {"max-queues": 200, "max-connections": 80, "other-key": payload_secret},
                },
                {"component": "plugin", "name": "anything", "vhost": "/unrelated", "value": [payload_secret]},
            ],
        }
    )
    auditor = ClusterAuditor(definitions)
    inventory = auditor.vhost_permissions_inventory()
    empty, other, payments = inventory.vhosts

    assert (empty.vhost, other.vhost, payments.vhost) == ("/empty", "/other", "/payments")
    assert empty.permissions == ()
    assert empty.topic_permissions == ()
    assert empty.max_queues == empty.max_connections == VhostLimit(VhostLimitStatus.UNSET)
    assert other.permissions == (VhostPermission("payments-api", "^$", ".*", ""),)
    assert other.topic_permissions == (VhostTopicPermission("payments-api", "", topic["write"], topic["read"]),)
    assert payments.permissions == (
        VhostPermission("admin", "(", grant["write"], ""),
        VhostPermission("payments-api", "^$", grant["write"], ""),
    )
    assert payments.topic_permissions == (
        VhostTopicPermission("payments-api", "archived", topic["write"], topic["read"]),
        VhostTopicPermission("payments-api", "events", topic["write"], topic["read"]),
    )
    assert payments.max_queues == VhostLimit(VhostLimitStatus.CAPPED, 200)
    assert payments.max_connections == VhostLimit(VhostLimitStatus.CAPPED, 80)
    assert not auditor.topology.nodes
    assert not auditor.topology.edges

    serialized = pickle.dumps(auditor.topology)
    restored = ClusterAuditor.from_topology(pickle.loads(serialized))
    report = restored.audit()
    assert report.vhost_permissions_inventory == restored.vhost_permissions_inventory() == inventory
    with pytest.raises(TopologyAnalysisError, match="Definitions are unavailable"):
        _ = restored.definitions
    for secret in (password_hash, payload_secret):
        assert secret.encode() not in serialized
        assert secret not in repr(auditor.topology)
        assert secret not in repr(asdict(inventory))
        assert secret not in repr(report)


@pytest.mark.parametrize("vhosts", [[], [{"name": "/payments"}]])
@pytest.mark.parametrize("section", ["permissions", "topic_permissions"])
def test_omitted_permission_sections_differ_from_explicit_empty_lists(
    vhosts: list[dict[str, str]], section: str
) -> None:
    omitted = ClusterAuditor(
        ClusterDefinitionsResponse.model_validate({"vhosts": vhosts})
    ).vhost_permissions_inventory()
    empty = ClusterAuditor(
        ClusterDefinitionsResponse.model_validate({"vhosts": vhosts, section: []})
    ).vhost_permissions_inventory()

    assert not getattr(omitted, f"{section}_captured")
    assert getattr(empty, f"{section}_captured")
    assert empty != omitted
    if vhosts:
        assert getattr(omitted.vhosts[0], section) is None
        assert getattr(empty.vhosts[0], section) == ()


def test_hand_built_graph_has_vhosts_with_unavailable_capture_evidence() -> None:
    queue = QueueNode(NodeId("/resource", "q", NodeKind.QUEUE), "classic", True)
    topology = ClusterTopology(
        frozenset(),
        frozenset({queue}),
        frozenset(),
        frozenset(),
        vhosts=frozenset({VirtualHostMetadata("/empty")}),
    )

    auditor = ClusterAuditor.from_topology(topology)
    assert auditor.vhost_permissions_inventory() == VhostPermissionsInventory(
        (VhostPermissions("/empty"), VhostPermissions("/resource"))
    )
