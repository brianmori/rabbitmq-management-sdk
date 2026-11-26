"""Check broker-version validation through the management client and its HTTP adapter."""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import TYPE_CHECKING
from unittest.mock import patch

import httpx2
import pytest

from rabbitmq_management_sdk import (
    Amqp091ShovelDestination,
    Amqp091ShovelSource,
    Amqp10ShovelSource,
    Config,
    LocalShovelSource,
    OperatorPolicyApplyTo,
    OperatorPolicyRequest,
    PolicyApplyTo,
    PolicyDefinition,
    PolicyRequest,
    QueueRequest,
    QuorumQueueRequest,
    RabbitMQClient,
    RabbitMQError,
    RabbitMQVersion,
    ShovelRequest,
    UnsupportedBrokerFeatureError,
)
from rabbitmq_management_sdk.http_adapter.httpx2 import Httpx2Adapter
from rabbitmq_management_sdk.resources.v4.queues.services import QueueManager

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.integration


@contextmanager
def _client(
    version: str, strict: bool, *, version_override: RabbitMQVersion | None = None
) -> Iterator[tuple[RabbitMQClient, list[httpx2.Request]]]:
    calls: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        if request.method == "GET":
            assert request.url.path == "/api/overview"
            return httpx2.Response(200, json={"rabbitmq_version": version})
        assert request.method == "PUT"
        return httpx2.Response(201)

    adapter = Httpx2Adapter(host="localhost", port=15672, transport=httpx2.MockTransport(handler))
    config = Config(
        host="localhost",
        username="local-test",
        password="",
        strict=strict,
        version_override=version_override,
    )
    with (
        patch("rabbitmq_management_sdk.client.rabbitmq_client.factory.create_adapter", return_value=adapter),
        RabbitMQClient(config) as client,
    ):
        yield client, calls


def _assert_blocked(error: UnsupportedBrokerFeatureError, feature: str) -> None:
    assert isinstance(error, RabbitMQError)
    assert error.feature == feature
    assert error.broker_version == "4.2.9"
    assert error.minimum_version == "4.3.0"


@pytest.mark.parametrize("strict", [False, True], ids=["compatibility", "strict"])
@pytest.mark.parametrize(
    ("feature", "value"),
    [
        ("x-delayed-retry-type", "disabled"),
        ("x-delayed-retry-min", 1_000),
        ("x-delayed-retry-max", 30_000),
        ("x-consumer-disconnected-timeout", 0),
    ],
)
def test_queue_manager_rejects_43_arguments_on_42(strict: bool, feature: str, value: str | int) -> None:
    request = QueueRequest(arguments=QuorumQueueRequest.model_validate({feature: value}))

    with _client("4.2.9", strict) as (client, calls):
        with pytest.raises(UnsupportedBrokerFeatureError) as raised:
            client.queues.create("versioned", request)
        _assert_blocked(raised.value, feature)
        assert [call.method for call in calls] == ["GET"]

    with _client("4.3.8", strict) as (client, calls):
        client.queues.create("versioned", request)
        assert [call.method for call in calls] == ["GET", "PUT"]
        assert calls[1].url.raw_path == b"/api/queues/%2F/versioned"
        arguments = {"x-queue-type": "quorum", feature: value}
        if strict:
            arguments.update({"x-delivery-limit": 20, "x-overflow": "drop-head"})
        assert json.loads(calls[1].content) == {"durable": True, "auto_delete": False, "arguments": arguments}


def _write_policy(client: RabbitMQClient, definition: PolicyDefinition, *, operator: bool) -> None:
    if operator:
        client.operator_policies.create(
            "versioned",
            OperatorPolicyRequest(
                pattern="^work$",
                definition=definition,
                priority=1,
                apply_to=OperatorPolicyApplyTo.QUORUM_QUEUES,
            ),
        )
    else:
        client.policies.create(
            "versioned",
            PolicyRequest(pattern="^work$", definition=definition, priority=1, apply_to=PolicyApplyTo.QUORUM_QUEUES),
        )


@pytest.mark.parametrize("strict", [False, True], ids=["compatibility", "strict"])
@pytest.mark.parametrize("operator", [False, True], ids=["regular", "operator"])
@pytest.mark.parametrize(
    ("feature", "value"),
    [
        ("delayed-retry-type", "disabled"),
        ("delayed-retry-min", 1_000),
        ("delayed-retry-max", 30_000),
        ("consumer-disconnected-timeout", 0),
    ],
)
def test_policy_managers_reject_43_definitions_on_42(
    strict: bool, operator: bool, feature: str, value: str | int
) -> None:
    definition = PolicyDefinition.model_validate({feature: value})

    with _client("4.2.9", strict) as (client, calls):
        with pytest.raises(UnsupportedBrokerFeatureError) as raised:
            _write_policy(client, definition, operator=operator)
        _assert_blocked(raised.value, feature)
        assert [call.method for call in calls] == ["GET"]

    with _client("4.3.8", strict) as (client, calls):
        _write_policy(client, definition, operator=operator)
        assert [call.method for call in calls] == ["GET", "PUT"]
        endpoint = "operator-policies" if operator else "policies"
        assert calls[1].url.raw_path == f"/api/{endpoint}/%2F/versioned".encode()
        assert json.loads(calls[1].content) == {
            "pattern": "^work$",
            "definition": {feature: value},
            "priority": 1,
            "apply-to": "quorum_queues",
        }


@pytest.mark.parametrize("strict", [False, True], ids=["compatibility", "strict"])
@pytest.mark.parametrize(
    "source",
    [
        Amqp091ShovelSource(src_queue="source", src_consumer_name="archive-reader"),
        Amqp10ShovelSource(src_address="/queues/source", src_consumer_name="archive-reader"),
        LocalShovelSource(src_queue="source", src_consumer_name="archive-reader"),
    ],
    ids=["amqp091", "amqp10", "local"],
)
def test_shovel_manager_rejects_named_consumers_on_42(
    strict: bool, source: Amqp091ShovelSource | Amqp10ShovelSource | LocalShovelSource
) -> None:
    request = ShovelRequest(
        src_uri="amqp://source",
        dest_uri="amqp://destination",
        src_arguments=source,
        dest_arguments=Amqp091ShovelDestination(dest_queue="archive"),
    )

    with _client("4.2.9", strict) as (client, calls):
        with pytest.raises(UnsupportedBrokerFeatureError) as raised:
            client.shovels.create("versioned", request)
        _assert_blocked(raised.value, "src-consumer-name")
        assert [call.method for call in calls] == ["GET"]

    with _client("4.3.8", strict) as (client, calls):
        client.shovels.create("versioned", request)
        assert [call.method for call in calls] == ["GET", "PUT"]
        assert calls[1].url.raw_path == b"/api/parameters/shovel/%2F/versioned"
        assert json.loads(calls[1].content) == {"value": request.to_api_value()}


@pytest.mark.parametrize("strict", [False, True], ids=["compatibility", "strict"])
def test_42_preserves_legacy_timeout_fields_and_custom_policy_keys(strict: bool) -> None:
    wire_definition = {"consumer-timeout": 60_000, "plugin-setting": {"enabled": True}}
    definition = PolicyDefinition.model_validate(wire_definition)

    with _client("4.2.9", strict) as (client, calls):
        client.queues.create("versioned", QueueRequest(arguments=QuorumQueueRequest(consumer_timeout=60_000)))
        _write_policy(client, definition, operator=False)
        _write_policy(client, definition, operator=True)

    assert [call.method for call in calls] == ["GET", "PUT", "PUT", "PUT"]
    assert json.loads(calls[1].content)["arguments"]["x-consumer-timeout"] == 60_000
    for call in calls[2:]:
        assert json.loads(call.content)["definition"] == wire_definition


def test_version_override_validates_without_discovering_the_broker() -> None:
    request = QueueRequest(arguments=QuorumQueueRequest(consumer_disconnected_timeout=0))

    with _client("4.3.8", strict=False, version_override=RabbitMQVersion.parse("4.2.9")) as (client, calls):
        with pytest.raises(UnsupportedBrokerFeatureError) as raised:
            client.queues.create("versioned", request)
        _assert_blocked(raised.value, "x-consumer-disconnected-timeout")
        assert not calls


def test_direct_manager_can_supply_a_broker_version() -> None:
    calls: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        return httpx2.Response(201)

    request = QueueRequest(arguments=QuorumQueueRequest(delayed_retry_min=1_000))
    with Httpx2Adapter(host="localhost", port=15672, transport=httpx2.MockTransport(handler)) as adapter:
        versioned = QueueManager(adapter, "%2F", False, broker_version=RabbitMQVersion.parse("4.2.9"))
        with pytest.raises(UnsupportedBrokerFeatureError) as raised:
            versioned.create("versioned", request)
        _assert_blocked(raised.value, "x-delayed-retry-min")
        assert not calls

        QueueManager(adapter, "%2F", False).create("versioned", request)

    (call,) = calls
    assert call.method == "PUT"
    assert json.loads(call.content)["arguments"]["x-delayed-retry-min"] == 1_000
