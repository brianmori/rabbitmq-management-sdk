from __future__ import annotations

import json

import httpx2
import pytest

from rabbitmq_management_sdk import ClassicQueueRequest, QueueRequest, QuorumQueueRequest, StreamQueueRequest
from rabbitmq_management_sdk.http_adapter.httpx2 import Httpx2Adapter
from rabbitmq_management_sdk.resources.v4.queues.schemas.queue_response import QuorumQueueResponse, StreamQueueResponse
from rabbitmq_management_sdk.resources.v4.queues.services import QueueManager


@pytest.mark.integration
def test_create_queue_success() -> None:
    # 1. Setup Mock Handler
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(201, json={"status": "created"}, headers={"Content-Type": "application/json"})

    # 2. Inject MockTransport into your Adapter
    mock_transport = httpx2.MockTransport(handler)
    adapter = Httpx2Adapter(host="localhost", port=15672, transport=mock_transport)

    # 3. Test the high-level request
    response = adapter.request("PUT", "/api/queues/%2f/my-queue", json={"durable": True})

    assert response.status_code == 201
    assert response.headers["Content-Type"] == "application/json"


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
    payload = sent[0]
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

    wire_arguments = sent[0]["arguments"]
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

    wire_arguments = sent[0]["arguments"]
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
