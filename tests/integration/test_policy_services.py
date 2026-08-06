"""Integration tests for regular and operator policy managers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from rabbitmq_management_sdk.http_adapter.httpx import HttpxAdapter
from rabbitmq_management_sdk.resources.v4.policies.schemas import (
    OperatorPolicyApplyTo,
    OperatorPolicyRequest,
    OperatorPolicyResponse,
    PolicyApplyTo,
    PolicyDefinition,
    PolicyRequest,
    PolicyResponse,
)
from rabbitmq_management_sdk.resources.v4.policies.services import (
    OperatorPolicyManager,
    PolicyManager,
)

_CAPTURE_PATH = Path(__file__).parent / "rmq-4.2" / "full-export-definitions.json"


def _adapter(handler: httpx.MockTransport) -> HttpxAdapter:
    return HttpxAdapter(host="localhost", port=15672, transport=handler)


@pytest.mark.integration
def test_regular_policy_manager_crud_and_listing() -> None:
    policy_name = "orders/team?primary#blue"
    encoded_policy_name = "orders%2Fteam%3Fprimary%23blue"
    wire_policy = {
        "vhost": "/",
        "name": policy_name,
        "pattern": "^orders\\.q$",
        "definition": {"dead-letter-exchange": "orders.dlx"},
        "priority": 10,
        "apply-to": "queues",
    }
    calls: list[tuple[str, str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.raw_path.decode(), body))
        if request.method == "GET" and request.url.raw_path.endswith(encoded_policy_name.encode()):
            return httpx.Response(200, json=wire_policy)
        if request.method == "GET":
            return httpx.Response(200, json=[wire_policy])
        return httpx.Response(204)

    manager = PolicyManager(
        http_client=_adapter(httpx.MockTransport(handler)),
        vhost="%2F",
        strict=False,
    )
    request = PolicyRequest(
        pattern="^orders\\.q$",
        definition=PolicyDefinition(dead_letter_exchange="orders.dlx"),
        priority=10,
        apply_to=PolicyApplyTo.QUEUES,
    )

    assert manager.get(policy_name).name == policy_name
    assert [policy.name for policy in manager.list_by_vhost()] == [policy_name]
    assert [policy.name for policy in manager.list_all()] == [policy_name]
    manager.create(policy_name, request)
    manager.delete(policy_name)

    assert calls == [
        ("GET", f"/api/policies/%2F/{encoded_policy_name}", None),
        ("GET", "/api/policies/%2F", None),
        ("GET", "/api/policies", None),
        (
            "PUT",
            f"/api/policies/%2F/{encoded_policy_name}",
            {
                "pattern": "^orders\\.q$",
                "definition": {"dead-letter-exchange": "orders.dlx"},
                "priority": 10,
                "apply-to": "queues",
            },
        ),
        ("DELETE", f"/api/policies/%2F/{encoded_policy_name}", None),
    ]


@pytest.mark.integration
def test_operator_policy_manager_uses_operator_endpoints() -> None:
    policy_name = "queue/limit?primary#blue"
    encoded_policy_name = "queue%2Flimit%3Fprimary%23blue"
    wire_policy = {
        "vhost": "policy",
        "name": policy_name,
        "pattern": ".*",
        "definition": {"max-length": 1000},
        "priority": 20,
        "apply-to": "queues",
    }
    calls: list[tuple[str, str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.raw_path.decode(), body))
        if request.method == "GET" and request.url.raw_path.endswith(encoded_policy_name.encode()):
            return httpx.Response(200, json=wire_policy)
        if request.method == "GET":
            return httpx.Response(200, json=[wire_policy])
        return httpx.Response(204)

    manager = OperatorPolicyManager(
        http_client=_adapter(httpx.MockTransport(handler)),
        vhost="policy",
        strict=True,
    )
    request = OperatorPolicyRequest(
        pattern=".*",
        definition=PolicyDefinition(max_length=1000),
        priority=20,
        apply_to=OperatorPolicyApplyTo.QUEUES,
    )

    assert manager.get(policy_name).definition.max_length == 1000
    assert [policy.name for policy in manager.list_by_vhost()] == [policy_name]
    assert [policy.name for policy in manager.list_all()] == [policy_name]
    manager.create(policy_name, request)
    manager.delete(policy_name)

    assert [call[:2] for call in calls] == [
        ("GET", f"/api/operator-policies/policy/{encoded_policy_name}"),
        ("GET", "/api/operator-policies/policy"),
        ("GET", "/api/operator-policies"),
        ("PUT", f"/api/operator-policies/policy/{encoded_policy_name}"),
        ("DELETE", f"/api/operator-policies/policy/{encoded_policy_name}"),
    ]
    assert calls[3][2] == {
        "pattern": ".*",
        "definition": {"max-length": 1000},
        "priority": 20,
        "apply-to": "queues",
    }


@pytest.mark.integration
def test_policy_schemas_accept_captured_rabbitmq_42_definitions() -> None:
    captured = json.loads(_CAPTURE_PATH.read_text())

    regular = [PolicyResponse.model_validate(policy) for policy in captured["policies"]]
    operator = [
        OperatorPolicyResponse.model_validate(
            {
                "vhost": parameter["vhost"],
                "name": parameter["name"],
                **parameter["value"],
            }
        )
        for parameter in captured["parameters"]
        if parameter["component"] == "operator_policy"
    ]

    assert {policy.name for policy in regular} >= {"all-quorum-q", "all-stream", "all-exchanges"}
    assert {policy.name for policy in operator} >= {"all-queues", "classic-queues", "quorum.q"}
    assert next(policy for policy in regular if policy.name == "all-quorum-q").definition.delivery_limit == 30
    assert next(policy for policy in operator if policy.name == "quorum.q").definition.target_group_size == 3232
