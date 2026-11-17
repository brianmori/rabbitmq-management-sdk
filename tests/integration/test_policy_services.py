"""Integration tests for regular and operator policy managers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx2
import pytest

from rabbitmq_management_sdk.http_adapter.httpx2 import Httpx2Adapter
from rabbitmq_management_sdk.resources.v4.common import DelayedRetryType
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


@pytest.mark.integration
@pytest.mark.parametrize("strict", [False, True], ids=["compatibility", "strict"])
@pytest.mark.parametrize("operator", [False, True], ids=["regular", "operator"])
def test_policy_manager_sends_quorum_configuration(strict: bool, operator: bool) -> None:
    sent: list[dict[str, object]] = []
    endpoint = "operator-policies" if operator else "policies"

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "PUT"
        assert request.url.raw_path == f"/api/{endpoint}/%2F/retry".encode()
        sent.append(json.loads(request.content))
        return httpx2.Response(201)

    definition = PolicyDefinition(
        delayed_retry_type=DelayedRetryType.DISABLED,
        delayed_retry_min=1000,
        delayed_retry_max=30_000,
        consumer_timeout=None if operator else 0,
        consumer_disconnected_timeout=None if operator else 0,
    )
    with _adapter(httpx2.MockTransport(handler)) as adapter:
        if operator:
            OperatorPolicyManager(adapter, "%2F", strict).create(
                "retry",
                OperatorPolicyRequest(
                    pattern="^work$",
                    definition=definition,
                    priority=1,
                    apply_to=OperatorPolicyApplyTo.QUORUM_QUEUES,
                ),
            )
        else:
            PolicyManager(adapter, "%2F", strict).create(
                "retry",
                PolicyRequest(
                    pattern="^work$", definition=definition, priority=1, apply_to=PolicyApplyTo.QUORUM_QUEUES
                ),
            )
    expected: dict[str, object] = {
        "delayed-retry-type": "disabled",
        "delayed-retry-min": 1000,
        "delayed-retry-max": 30_000,
    }
    if not operator:
        expected.update({"consumer-timeout": 0, "consumer-disconnected-timeout": 0})
    assert sent == [{"pattern": "^work$", "definition": expected, "priority": 1, "apply-to": "quorum_queues"}]


def _adapter(handler: httpx2.MockTransport) -> Httpx2Adapter:
    return Httpx2Adapter(host="localhost", port=15672, transport=handler)


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

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.raw_path.decode(), body))
        if request.method == "GET" and request.url.raw_path.endswith(encoded_policy_name.encode()):
            return httpx2.Response(200, json=wire_policy)
        if request.method == "GET":
            return httpx2.Response(200, json=[wire_policy])
        return httpx2.Response(204)

    manager = PolicyManager(
        http_client=_adapter(httpx2.MockTransport(handler)),
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

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.raw_path.decode(), body))
        if request.method == "GET" and request.url.raw_path.endswith(encoded_policy_name.encode()):
            return httpx2.Response(200, json=wire_policy)
        if request.method == "GET":
            return httpx2.Response(200, json=[wire_policy])
        return httpx2.Response(204)

    manager = OperatorPolicyManager(
        http_client=_adapter(httpx2.MockTransport(handler)),
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
