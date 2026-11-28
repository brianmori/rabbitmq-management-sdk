from __future__ import annotations

import json

import httpx2
import pytest

from rabbitmq_management_sdk import (
    BadRequestError,
    ClassicQueueRequest,
    DelayedRetryType,
    QueueDeleteOptions,
    QueueRequest,
    QuorumQueueRequest,
    StreamQueueRequest,
)
from rabbitmq_management_sdk.http_adapter.httpx2 import Httpx2Adapter
from rabbitmq_management_sdk.http_adapter.retry import RetryTransport
from rabbitmq_management_sdk.resources.v4.queues.schemas.queue_response import QuorumQueueResponse, StreamQueueResponse
from rabbitmq_management_sdk.resources.v4.queues.services import QueueManager


@pytest.mark.integration
@pytest.mark.parametrize(
    ("kwargs", "query"),
    [
        pytest.param({}, b"", id="omitted"),
        pytest.param({"options": QueueDeleteOptions()}, b"", id="neither"),
        pytest.param({"options": QueueDeleteOptions(if_empty=True)}, b"?if-empty=true", id="if-empty"),
        pytest.param({"options": QueueDeleteOptions(if_unused=True)}, b"?if-unused=true", id="if-unused"),
        pytest.param(
            {"options": QueueDeleteOptions(if_empty=True, if_unused=True)},
            b"?if-empty=true&if-unused=true",
            id="both",
        ),
    ],
)
def test_queue_delete_sends_conditions_on_one_request(kwargs: dict[str, QueueDeleteOptions], query: bytes) -> None:
    sent: list[tuple[str, bytes, bytes]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append((request.method, request.url.raw_path, request.content))
        return httpx2.Response(204)

    with Httpx2Adapter(host="localhost", port=15672, transport=httpx2.MockTransport(handler)) as adapter:
        QueueManager(adapter, "%2F", False).delete("orders/eu?#% café", **kwargs)

    assert sent == [("DELETE", b"/api/queues/%2F/orders%2Feu%3F%23%25%20caf%C3%A9" + query, b"")]


@pytest.mark.integration
def test_queue_delete_propagates_broker_refusal_without_retrying() -> None:
    sent: list[tuple[str, bytes]] = []
    reason = "406 PRECONDITION_FAILED - queue 'orders' in vhost '/' not empty"

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append((request.method, request.url.raw_path))
        return httpx2.Response(400, json={"error": "bad_request", "reason": reason})

    with (
        RetryTransport(Httpx2Adapter(host="localhost", port=15672, transport=httpx2.MockTransport(handler))) as adapter,
        pytest.raises(BadRequestError) as exc,
    ):
        QueueManager(adapter, "%2F", False).delete("orders", options=QueueDeleteOptions(if_empty=True))

    assert exc.value.status_code == 400
    assert exc.value.reason == reason
    assert sent == [("DELETE", b"/api/queues/%2F/orders?if-empty=true")]


@pytest.mark.integration
@pytest.mark.parametrize("strict", [False, True], ids=["compatibility", "strict"])
@pytest.mark.parametrize(
    "arguments",
    [ClassicQueueRequest(), QuorumQueueRequest(), StreamQueueRequest()],
    ids=["classic", "quorum", "stream"],
)
def test_queue_declaration_sends_explicit_type_in_arguments(
    strict: bool, arguments: ClassicQueueRequest | QuorumQueueRequest | StreamQueueRequest
) -> None:
    sent: list[dict[str, object]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "PUT"
        assert request.url.raw_path == b"/api/queues/%2F/typed-queue"
        sent.append(json.loads(request.content))
        return httpx2.Response(201)

    with Httpx2Adapter(host="localhost", port=15672, transport=httpx2.MockTransport(handler)) as adapter:
        manager = QueueManager(adapter, "%2F", strict)
        manager.create("typed-queue", QueueRequest(arguments=arguments))

    assert len(sent) == 1
    (payload,) = sent
    assert "x-queue-type" not in payload
    assert payload["durable"] is True
    assert payload["auto_delete"] is False
    wire_arguments = payload["arguments"]
    assert isinstance(wire_arguments, dict)
    assert wire_arguments["x-queue-type"] == arguments.queue_type
    if arguments.queue_type in {"classic", "quorum"}:
        assert ("x-overflow" in wire_arguments) is strict
    if arguments.queue_type == "quorum":
        assert ("x-delivery-limit" in wire_arguments) is strict
        assert payload == {
            "durable": True,
            "auto_delete": False,
            "arguments": {"x-queue-type": "quorum", "x-delivery-limit": 20, "x-overflow": "drop-head"}
            if strict
            else {"x-queue-type": "quorum"},
        }


@pytest.mark.integration
@pytest.mark.parametrize("strict", [False, True], ids=["compatibility", "strict"])
@pytest.mark.parametrize("retry_type", list(DelayedRetryType))
def test_queue_manager_sends_quorum_retry_and_timeout_configuration(strict: bool, retry_type: DelayedRetryType) -> None:
    sent: list[dict[str, object]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "PUT"
        assert request.url.raw_path == b"/api/queues/%2F/retry"
        sent.append(json.loads(request.content))
        return httpx2.Response(201)

    arguments = QuorumQueueRequest(
        delayed_retry_type=retry_type,
        delayed_retry_min=1000,
        delayed_retry_max=30_000,
        consumer_timeout=0,
        consumer_disconnected_timeout=0,
    )
    with Httpx2Adapter(host="localhost", port=15672, transport=httpx2.MockTransport(handler)) as adapter:
        QueueManager(adapter, "%2F", strict).create("retry", QueueRequest(arguments=arguments))

    expected: dict[str, object] = {
        "x-queue-type": "quorum",
        "x-delayed-retry-type": retry_type.value,
        "x-delayed-retry-min": 1000,
        "x-delayed-retry-max": 30_000,
        "x-consumer-timeout": 0,
        "x-consumer-disconnected-timeout": 0,
    }
    if strict:
        expected.update({"x-delivery-limit": 20, "x-overflow": "drop-head"})
    assert sent == [{"durable": True, "auto_delete": False, "arguments": expected}]


@pytest.mark.integration
@pytest.mark.parametrize("strict", [False, True], ids=["compatibility", "strict"])
@pytest.mark.parametrize(
    ("arguments", "wire_key", "wire_value"),
    [
        (ClassicQueueRequest(message_ttl=60_000), "x-message-ttl", 60_000),
        (QuorumQueueRequest(delivery_limit=5), "x-delivery-limit", 5),
        (StreamQueueRequest(max_age="7d"), "x-max-age", "7d"),
    ],
    ids=["classic", "quorum", "stream"],
)
def test_queue_declaration_preserves_nondefault_arguments(
    strict: bool,
    arguments: ClassicQueueRequest | QuorumQueueRequest | StreamQueueRequest,
    wire_key: str,
    wire_value: str | int,
) -> None:
    sent: list[dict[str, object]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        return httpx2.Response(201)

    with Httpx2Adapter(host="localhost", port=15672, transport=httpx2.MockTransport(handler)) as adapter:
        QueueManager(adapter, "%2F", strict).create("configured-queue", QueueRequest(arguments=arguments))

    (payload,) = sent
    wire_arguments = payload["arguments"]
    assert isinstance(wire_arguments, dict)
    assert wire_arguments["x-queue-type"] == arguments.queue_type
    assert wire_arguments[wire_key] == wire_value


@pytest.mark.integration
@pytest.mark.parametrize("strict", [False, True], ids=["compatibility", "strict"])
@pytest.mark.parametrize(
    ("arguments", "wire_key", "other_queue_type_key"),
    [
        (QuorumQueueRequest(initial_cluster_size=5), "x-quorum-initial-group-size", "x-initial-cluster-size"),
        (StreamQueueRequest(initial_cluster_size=5), "x-initial-cluster-size", "x-quorum-initial-group-size"),
    ],
    ids=["quorum", "stream"],
)
def test_queue_declaration_sends_type_specific_initial_replication_size(
    strict: bool,
    arguments: QuorumQueueRequest | StreamQueueRequest,
    wire_key: str,
    other_queue_type_key: str,
) -> None:
    sent: list[dict[str, object]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "PUT"
        sent.append(json.loads(request.content))
        return httpx2.Response(201)

    with Httpx2Adapter(host="localhost", port=15672, transport=httpx2.MockTransport(handler)) as adapter:
        QueueManager(adapter, "%2F", strict).create("replicated-queue", QueueRequest(arguments=arguments))

    (payload,) = sent
    wire_arguments = payload["arguments"]
    assert isinstance(wire_arguments, dict)
    assert wire_arguments["x-queue-type"] == arguments.queue_type
    assert wire_arguments[wire_key] == 5
    assert other_queue_type_key not in wire_arguments


@pytest.mark.integration
@pytest.mark.parametrize(
    ("queue_type", "wire_key"),
    [("quorum", "x-quorum-initial-group-size"), ("stream", "x-initial-cluster-size")],
)
def test_queue_response_parses_type_specific_initial_replication_size(queue_type: str, wire_key: str) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "GET"
        return httpx2.Response(
            200,
            json={
                "name": "replicated-queue",
                "vhost": "/",
                "durable": True,
                "auto_delete": False,
                "exclusive": False,
                "type": queue_type,
                "node": "rabbit@node",
                "state": "running",
                "arguments": {"x-queue-type": queue_type, wire_key: 5},
            },
        )

    with Httpx2Adapter(host="localhost", port=15672, transport=httpx2.MockTransport(handler)) as adapter:
        queue = QueueManager(adapter, "%2F", False).get("replicated-queue")

    assert isinstance(queue.arguments, (QuorumQueueResponse, StreamQueueResponse))
    assert queue.arguments.queue_type == queue_type
    assert queue.arguments.initial_cluster_size == 5
    assert queue.arguments.model_dump(by_alias=True, exclude_none=True)[wire_key] == 5
