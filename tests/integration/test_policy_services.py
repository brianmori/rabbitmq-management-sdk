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
    wire_policy = {
        "vhost": "/",
        "name": "orders-policy",
        "pattern": "^orders\\.q$",
        "definition": {"dead-letter-exchange": "orders.dlx"},
        "priority": 10,
        "apply-to": "queues",
    }
    calls: list[tuple[str, str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.raw_path.decode(), body))
        if request.method == "GET" and request.url.raw_path.endswith(b"/orders-policy"):
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

    assert manager.get("orders-policy").name == "orders-policy"
    assert [policy.name for policy in manager.list_by_vhost()] == ["orders-policy"]
    assert [policy.name for policy in manager.list_all()] == ["orders-policy"]
    manager.create("orders-policy", request)
    manager.delete("orders-policy")

    assert calls == [
        ("GET", "/api/policies/%2F/orders-policy", None),
        ("GET", "/api/policies/%2F", None),
        ("GET", "/api/policies", None),
        (
            "PUT",
            "/api/policies/%2F/orders-policy",
            {
                "pattern": "^orders\\.q$",
                "definition": {"dead-letter-exchange": "orders.dlx"},
                "priority": 10,
                "apply-to": "queues",
            },
        ),
        ("DELETE", "/api/policies/%2F/orders-policy", None),
    ]


@pytest.mark.integration
def test_operator_policy_manager_uses_operator_endpoints() -> None:
    wire_policy = {
        "vhost": "policy",
        "name": "queue-limit",
        "pattern": ".*",
        "definition": {"max-length": 1000},
        "priority": 20,
        "apply-to": "queues",
    }
    calls: list[tuple[str, str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.raw_path.decode(), body))
        if request.method == "GET" and request.url.raw_path.endswith(b"/queue-limit"):
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

    assert manager.get("queue-limit").definition.max_length == 1000
    assert [policy.name for policy in manager.list_by_vhost()] == ["queue-limit"]
    assert [policy.name for policy in manager.list_all()] == ["queue-limit"]
    manager.create("queue-limit", request)
    manager.delete("queue-limit")

    assert [call[:2] for call in calls] == [
        ("GET", "/api/operator-policies/policy/queue-limit"),
        ("GET", "/api/operator-policies/policy"),
        ("GET", "/api/operator-policies"),
        ("PUT", "/api/operator-policies/policy/queue-limit"),
        ("DELETE", "/api/operator-policies/policy/queue-limit"),
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
