"""Shared factories for topology parser tests."""

from types import MappingProxyType
from typing import Any

from rabbitmq_management_sdk.resources.v4.admin.schemas.export_response import ClusterDefinitionsResponse
from rabbitmq_management_sdk.topology.models import NodeId, NodeKind
from rabbitmq_management_sdk.topology.policy_routes import UserPolicySelections


def _response(**overrides: object) -> ClusterDefinitionsResponse:
    """Build a minimal validated definitions response."""
    base: dict[str, Any] = {
        "users": [],
        "vhosts": [],
        "permissions": [],
        "topic_permissions": [],
        "global_parameters": [],
        "parameters": [],
        "policies": [],
        "queues": [],
        "exchanges": [],
        "bindings": [],
    }
    base.update(overrides)
    return ClusterDefinitionsResponse.model_validate(base)


def _vhost(name: str, default_queue_type: str | None = "classic") -> dict[str, object]:
    """Build a vhost definition."""
    return {
        "name": name,
        "description": "",
        "tags": [],
        "metadata": {"description": "", "tags": [], "default_queue_type": default_queue_type},
    }


def _queue(name: str, vhost: str, **arguments: object) -> dict[str, object]:
    """Build a queue definition."""
    return {"name": name, "vhost": vhost, "durable": True, "auto_delete": False, "arguments": arguments}


def _exchange(name: str, vhost: str, type_: str = "direct", **arguments: object) -> dict[str, object]:
    """Build an exchange definition."""
    return {
        "name": name,
        "vhost": vhost,
        "type": type_,
        "durable": True,
        "auto_delete": False,
        "internal": False,
        "arguments": arguments,
    }


def _policy(
    name: str,
    pattern: str,
    apply_to: str,
    priority: int,
    *,
    vhost: str = "t",
    definition: dict[str, object] | None = None,
) -> dict[str, object]:
    """Build a user policy definition."""
    return {
        "vhost": vhost,
        "name": name,
        "pattern": pattern,
        "apply-to": apply_to,
        "priority": priority,
        "definition": definition or {},
    }


def _policy_selections(
    *,
    vhost: str,
    name: str,
    kind: NodeKind,
    policy_name: str | None,
    cluster_id: str | None = None,
) -> UserPolicySelections:
    """Build observed user-policy selection evidence."""
    return MappingProxyType(
        {
            NodeId(cluster_id=cluster_id, vhost=vhost, name=name, kind=kind): policy_name,
        }
    )


def _shovel_param(name: str, vhost: str, **value: object) -> dict[str, object]:
    """Build a shovel parameter definition."""
    return {"vhost": vhost, "component": "shovel", "name": name, "value": value}
