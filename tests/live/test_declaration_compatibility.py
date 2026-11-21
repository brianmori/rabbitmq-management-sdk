"""Verify queue declarations and binding acceptance on disposable RabbitMQ brokers."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from rabbitmq_management_sdk import BindingRequest, ClassicQueueRequest, ClusterAuditor, ExchangeRequest, QueueRequest
from rabbitmq_management_sdk.topology import BindingDurabilityKind, NodeKind, QueueDeclarationStatus

if TYPE_CHECKING:
    from rabbitmq_management_sdk import RabbitMQClient
    from rabbitmq_management_sdk.resources.v4.exchanges.schemas.exchange_response import ExchangeResponse
    from rabbitmq_management_sdk.resources.v4.queues.schemas.queue_response import QueueResponse

pytestmark = pytest.mark.live


def _check_classic_declarations(vhost: str) -> str:
    container = os.getenv("RABBIT_TEST_CONTAINER")
    if container is None:
        pytest.skip("Native declarations require the disposable Compose live-test recipe")
    source = Path(__file__).with_name("check_classic_declarations.erl").read_text(encoding="utf-8")
    completed = subprocess.run(
        ["docker", "exec", container, "rabbitmqctl", "-q", "eval", f"Vhost = {json.dumps(vhost)}, " + source],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        pytest.fail("Disposable broker could not run the declaration probe", pytrace=False)
    outcome = completed.stdout.strip().split("\n")[-1]
    if outcome not in {"accepted", "denied_by_transient_nonexcl_rule"}:
        pytest.fail("Disposable broker returned an unexpected declaration result", pytrace=False)
    return outcome


def test_binding_durability_and_default_classic_declaration_behavior(
    live_client: RabbitMQClient, live_vhost: str
) -> None:
    outcome = _check_classic_declarations(live_vhost)
    expected = "accepted" if live_client.version.minor == 2 else "denied_by_transient_nonexcl_rule"
    assert outcome == expected
    for role in ("source", "target"):
        live_client.exchanges.create(f"{role}.durable", ExchangeRequest(type="direct", durable=True))
        live_client.exchanges.create(f"{role}.transient", ExchangeRequest(type="direct", durable=False))
    live_client.queues.create("work", QueueRequest(arguments=ClassicQueueRequest()))

    expected_bindings = {}
    classifications = {
        ("durable", "durable"): BindingDurabilityKind.FULLY_DURABLE,
        ("durable", "transient"): BindingDurabilityKind.SEMI_DURABLE,
        ("transient", "durable"): BindingDurabilityKind.UNCLASSIFIED,
        ("transient", "transient"): BindingDurabilityKind.FULLY_TRANSIENT,
    }
    queue_targets = [("work", "durable"), ("shared", "transient")] if outcome == "accepted" else [("work", "durable")]
    for (source, target), classification in classifications.items():
        source_name, target_name = f"source.{source}", f"target.{target}"
        live_client.bindings.create_exchange_to_exchange(source_name, target_name, BindingRequest(routing_key="route"))
        expected_bindings[(source_name, target_name)] = classification
    for queue, durability in queue_targets:
        for source in ("durable", "transient"):
            source_name = f"source.{source}"
            live_client.bindings.create_exchange_to_queue(source_name, queue, BindingRequest(routing_key="route"))
            expected_bindings[(source_name, queue)] = classifications[source, durability]

    queues = live_client.queues.list_all(disable_stats=True)
    exchanges = live_client.exchanges.list_all(disable_stats=True)
    auditor = ClusterAuditor(live_client.admin.export_definitions(), queues=queues, exchanges=exchanges)
    findings = [finding for finding in auditor.binding_durability() if finding.source.vhost == live_vhost]
    assert {(item.source.name, item.target.name): item.classification for item in findings} == expected_bindings
    assert {item.target.kind for item in findings} == {NodeKind.QUEUE, NodeKind.EXCHANGE}
    compatibility = [item for item in auditor.queue_declaration_compatibility() if item.queue.vhost == live_vhost]
    if outcome == "accepted":
        (shared,) = compatibility
        assert shared.queue.name == "shared"
        assert shared.exclusive is False
        assert shared.status == QueueDeclarationStatus.REJECTED_BY_DEFAULT
    else:
        assert not compatibility
    _capture(auditor, live_vhost, queues, exchanges, outcome)


def _capture(
    auditor: ClusterAuditor,
    vhost: str,
    queues: list[QueueResponse],
    exchanges: list[ExchangeResponse],
    outcome: str,
) -> None:
    output = os.getenv("RABBIT_DECLARATION_EVIDENCE_DIR")
    if output is None:
        return
    definitions = auditor.definitions
    version = definitions.rabbitmq_version
    entries = definitions.model_dump(mode="json", by_alias=True, exclude_unset=True)
    projection = {
        key: [{**entry, "vhost": "captured"} for entry in entries[key] if entry["vhost"] == vhost]
        for key in ("queues", "exchanges", "bindings")
    }
    (captured_vhost,) = [entry for entry in entries["vhosts"] if entry["name"] == vhost]
    captured_vhost["name"] = "captured"
    queue_fields = {
        "name",
        "vhost",
        "durable",
        "auto_delete",
        "exclusive",
        "policy",
        "operator_policy",
        "effective_policy_definition",
        "type",
        "node",
        "state",
        "arguments",
    }
    exchange_fields = {"name", "vhost", "durable", "auto_delete", "internal", "type", "arguments", "policy"}
    data = {
        "definitions": {
            **projection,
            "rabbitmq_version": version,
            "vhosts": [captured_vhost],
            "parameters": [],
            "policies": [],
        },
        "queues": [
            {
                **queue.model_dump(mode="json", by_alias=True, exclude_unset=True, include=queue_fields),
                "vhost": "captured",
                "node": "rabbit@captured",
            }
            for queue in queues
            if queue.vhost == vhost
        ],
        "exchanges": [
            {
                **exchange.model_dump(mode="json", by_alias=True, exclude_unset=True, include=exchange_fields),
                "vhost": "captured",
            }
            for exchange in exchanges
            if exchange.vhost == vhost
        ],
        "observations": {"private_declaration": "accepted", "private_exported": False, "shared_declaration": outcome},
    }
    path = Path(output)
    path.mkdir(parents=True, exist_ok=True)
    (path / f"rmq-{version}.json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
