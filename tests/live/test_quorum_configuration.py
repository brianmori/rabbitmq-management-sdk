"""RabbitMQ 4.3 configuration round trips; no message timing assumptions."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

import pytest
from tests.shared.live_broker import wait_for

from rabbitmq_management_sdk import (
    ClusterAuditor,
    DelayedRetryType,
    OperatorPolicyApplyTo,
    OperatorPolicyRequest,
    PolicyApplyTo,
    PolicyDefinition,
    PolicyRequest,
    QueueRequest,
    QuorumQueueRequest,
    RabbitMQClient,
)
from rabbitmq_management_sdk.resources.v4.queues.schemas.queue_response import QuorumQueueResponse
from rabbitmq_management_sdk.topology import ConfigurationSource

if TYPE_CHECKING:
    from tests.conftest import RabbitSettings
    from tests.shared.live_broker import ManagementAPI

pytestmark = pytest.mark.live


@pytest.fixture(autouse=True)
def require_rabbitmq43(rabbitmq_client_compatibility: RabbitMQClient) -> None:
    version = rabbitmq_client_compatibility.version
    if (version.major, version.minor) != (4, 3):
        pytest.skip("quorum retry and timeout configuration requires RabbitMQ 4.3; 4.4 is deferred")


@pytest.mark.parametrize("strict", [False, True], ids=["compatibility", "strict"])
@pytest.mark.parametrize("retry_type", list(DelayedRetryType))
def test_quorum_queue_and_policy_configuration_round_trip(
    rabbit_config: RabbitSettings,
    live_vhost: str,
    management_api: ManagementAPI,
    strict: bool,
    retry_type: DelayedRetryType,
) -> None:
    with RabbitMQClient(rabbit_config.client_config(virtual_host=live_vhost, strict=strict)) as client:
        arguments = QuorumQueueRequest(
            delayed_retry_type=retry_type,
            delayed_retry_min=1000,
            delayed_retry_max=30_000,
            consumer_timeout=120_000,
            consumer_disconnected_timeout=60_000,
        )
        client.queues.create("configured", QueueRequest(arguments=arguments))
        queue = client.queues.get("configured")
        assert isinstance(queue.arguments, QuorumQueueResponse)
        assert queue.arguments.delayed_retry_type == retry_type
        assert queue.arguments.delayed_retry_min == 1000
        assert queue.arguments.delayed_retry_max == 30_000
        assert queue.arguments.consumer_timeout == 120_000
        assert queue.arguments.consumer_disconnected_timeout == 60_000

        definition = PolicyDefinition(
            delayed_retry_type=retry_type,
            delayed_retry_min=2000,
            delayed_retry_max=40_000,
            consumer_timeout=180_000,
            consumer_disconnected_timeout=90_000,
        )
        client.policies.create(
            "retry",
            PolicyRequest(
                pattern="^policy-queue$", definition=definition, priority=1, apply_to=PolicyApplyTo.QUORUM_QUEUES
            ),
        )
        client.queues.create("policy-queue", QueueRequest(arguments=QuorumQueueRequest()))
        wait_for(lambda: client.queues.get("policy-queue").policy == "retry", "quorum policy selection")
        policy = client.policies.get("retry")
        assert policy.definition.model_dump() == definition.model_dump(by_alias=True, exclude_none=True)
        assert policy.definition.delayed_retry_type == retry_type
        assert policy.definition.delayed_retry_min == 2000
        assert policy.definition.delayed_retry_max == 40_000
        assert policy.definition.consumer_timeout == 180_000
        assert policy.definition.consumer_disconnected_timeout == 90_000

        exported = client.admin.export_definitions()
        captured_queue = next(q for q in exported.queues if q.vhost == live_vhost and q.name == "configured")
        assert captured_queue.arguments.delayed_retry_type == retry_type
        assert captured_queue.arguments.delayed_retry_min == 1000
        assert captured_queue.arguments.delayed_retry_max == 30_000
        assert captured_queue.arguments.consumer_timeout == 120_000
        assert captured_queue.arguments.consumer_disconnected_timeout == 60_000
        captured_policy = next(p for p in exported.policies if p.vhost == live_vhost and p.name == "retry")
        assert captured_policy.definition.model_dump() == policy.definition.model_dump()
        auditor = ClusterAuditor(
            exported,
            queues=client.queues.list_all(disable_stats=True),
            exchanges=client.exchanges.list_all(disable_stats=True),
        )
        results = {item.queue.name: item.configuration for item in auditor.quorum_delivery_configuration()}
        configured = results["configured"]
        assert configured.delayed_retry_type.value == retry_type
        assert configured.consumer_timeout.value == 120_000
        assert configured.consumer_disconnected_timeout.value == 60_000
        assert results["policy-queue"].delayed_retry_type.value == retry_type
        assert results["policy-queue"].consumer_timeout.value == 180_000
        assert results["policy-queue"].consumer_disconnected_timeout.value == 90_000
        if retry_type != DelayedRetryType.DISABLED:
            assert configured.delayed_retry_min.value == 1000
            assert configured.delayed_retry_max.value == 30_000
            assert results["policy-queue"].delayed_retry_min.value == 2000
            assert results["policy-queue"].delayed_retry_max.value == 40_000

        if not strict and retry_type == DelayedRetryType.ALL:
            _capture_configuration(client, live_vhost, management_api)


@pytest.mark.parametrize("strict", [False, True], ids=["compatibility", "strict"])
def test_quorum_operator_policy_retry_round_trip(rabbit_config: RabbitSettings, live_vhost: str, strict: bool) -> None:
    with RabbitMQClient(rabbit_config.client_config(virtual_host=live_vhost, strict=strict)) as client:
        definition = PolicyDefinition(delayed_retry_type=DelayedRetryType.FAILED, delayed_retry_min=1000)
        client.operator_policies.create(
            "retry",
            OperatorPolicyRequest(
                pattern="^work$", definition=definition, priority=1, apply_to=OperatorPolicyApplyTo.QUORUM_QUEUES
            ),
        )
        response = client.operator_policies.get("retry")
        assert response.definition.model_dump() == {"delayed-retry-type": "failed", "delayed-retry-min": 1000}


@pytest.mark.parametrize("strict", [False, True], ids=["compatibility", "strict"])
def test_audit_resolves_regular_operator_policy_and_arguments(
    rabbit_config: RabbitSettings,
    live_vhost: str,
    management_api: ManagementAPI,
    strict: bool,
) -> None:
    with RabbitMQClient(rabbit_config.client_config(virtual_host=live_vhost, strict=strict)) as client:
        client.policies.create(
            "retry",
            PolicyRequest(
                pattern="^configured$",
                priority=1,
                apply_to=PolicyApplyTo.QUORUM_QUEUES,
                definition=PolicyDefinition(
                    delayed_retry_type=DelayedRetryType.ALL,
                    delayed_retry_min=2000,
                    delayed_retry_max=12_000,
                    delivery_limit=40,
                    consumer_timeout=180_000,
                ),
            ),
        )
        client.operator_policies.create(
            "operator",
            OperatorPolicyRequest(
                pattern="^configured$",
                priority=1,
                apply_to=OperatorPolicyApplyTo.QUORUM_QUEUES,
                definition=PolicyDefinition(
                    delayed_retry_type=DelayedRetryType.RETURNED,
                    delayed_retry_min=3000,
                    delayed_retry_max=8000,
                    delivery_limit=25,
                ),
            ),
        )
        client.queues.create(
            "configured",
            QueueRequest(
                arguments=QuorumQueueRequest(
                    delayed_retry_type=DelayedRetryType.FAILED,
                    delayed_retry_min=1000,
                    delayed_retry_max=9000,
                    delivery_limit=-1,
                    consumer_timeout=120_000,
                )
            ),
        )
        wait_for(lambda: client.queues.get("configured").operator_policy == "operator", "operator policy selection")
        auditor = ClusterAuditor(
            client.admin.export_definitions(),
            queues=client.queues.list_all(disable_stats=True),
            exchanges=client.exchanges.list_all(disable_stats=True),
        )
        (result,) = auditor.quorum_delivery_configuration()
        config = result.configuration
        assert config.delayed_retry_type.value == "returned"
        assert config.delayed_retry_min.value == 1000
        assert config.delayed_retry_max.value == 8000
        assert config.delivery_limit.value == 25
        assert config.consumer_timeout.value == 120_000
        assert {item.source for item in config.delayed_retry_min.evidence} >= {
            ConfigurationSource.QUEUE_ARGUMENT,
            ConfigurationSource.REGULAR_POLICY,
            ConfigurationSource.OPERATOR_POLICY,
        }
        assert not auditor.structural_cycles().cycles
        if not strict:
            _capture_configuration(client, live_vhost, management_api, filename="operator-configuration.json")


def _capture_configuration(
    client: RabbitMQClient,
    vhost: str,
    management_api: ManagementAPI,
    *,
    filename: str = "configuration.json",
) -> None:
    """Write an explicitly requested, credential-free projection of broker JSON."""
    output = os.getenv("RABBIT_QUORUM_CONFIG_EVIDENCE_DIR")
    if not output:
        return
    queue = management_api.request("GET", f"queues/{quote(vhost, safe='')}/configured")
    policy = management_api.request("GET", f"policies/{quote(vhost, safe='')}/retry")
    definitions = management_api.request("GET", "definitions")
    assert isinstance(queue, dict) and isinstance(policy, dict) and isinstance(definitions, dict)
    queue_keys = {
        "name",
        "vhost",
        "durable",
        "auto_delete",
        "exclusive",
        "type",
        "node",
        "state",
        "arguments",
        "policy",
        "operator_policy",
        "effective_policy_definition",
        "members",
    }
    queue = {key: value for key, value in queue.items() if key in queue_keys}
    queue.update({"vhost": "captured", "node": "rabbit@captured-node"})
    policy["vhost"] = "captured"
    projection = {
        "rabbit_version": definitions["rabbit_version"],
        "queues": [{**entry, "vhost": "captured"} for entry in definitions["queues"] if entry["vhost"] == vhost],
        "policies": [{**entry, "vhost": "captured"} for entry in definitions["policies"] if entry["vhost"] == vhost],
        "parameters": [
            {**entry, "vhost": "captured"}
            for entry in definitions["parameters"]
            if entry["vhost"] == vhost and entry["component"] == "operator_policy"
        ],
    }
    observations = management_api.request("GET", "queues")
    exchanges = management_api.request("GET", "exchanges")
    assert isinstance(observations, list) and isinstance(exchanges, list)
    queues = []
    for observed in observations:
        if observed["vhost"] == vhost:
            captured = {key: value for key, value in observed.items() if key in queue_keys}
            captured.update({"vhost": "captured", "node": "rabbit@captured-node"})
            if isinstance(captured.get("members"), list):
                captured["members"] = [
                    "rabbit@captured-node" if index == 0 else f"rabbit@captured-node-{index}"
                    for index in range(len(captured["members"]))
                ]
            queues.append(captured)
    exchange_keys = {"name", "type", "durable", "auto_delete", "internal", "arguments", "policy"}
    captured_exchanges = [
        {**{key: value for key, value in entry.items() if key in exchange_keys}, "vhost": "captured"}
        for entry in exchanges
        if entry["vhost"] == vhost
    ]
    if isinstance(queue.get("members"), list):
        queue["members"] = [
            "rabbit@captured-node" if index == 0 else f"rabbit@captured-node-{index}"
            for index in range(len(queue["members"]))
        ]
    path = Path(output)
    path.mkdir(parents=True, exist_ok=True)
    (path / filename).write_text(
        json.dumps(
            {
                "broker_version": str(client.version),
                "queue": queue,
                "policy": policy,
                "definitions": projection,
                "queues": queues,
                "exchanges": captured_exchanges,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
