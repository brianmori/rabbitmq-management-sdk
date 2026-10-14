from __future__ import annotations

import json

import httpx
import pytest

from rabbitmq_management_sdk import ClassicQueueRequest, QueueRequest, QuorumQueueRequest, StreamQueueRequest
from rabbitmq_management_sdk.http_adapter.httpx import HttpxAdapter
from rabbitmq_management_sdk.resources.v4.queues.services import QueueManager


@pytest.mark.integration
def test_create_queue_success() -> None:
    # 1. Setup Mock Handler
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"status": "created"}, headers={"Content-Type": "application/json"})

    # 2. Inject MockTransport into your Adapter
    mock_transport = httpx.MockTransport(handler)
    adapter = HttpxAdapter(host="localhost", port=15672, transport=mock_transport)

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

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert request.url.raw_path == b"/api/queues/%2F/typed-queue"
        sent.append(json.loads(request.content))
        return httpx.Response(201)

    with HttpxAdapter(host="localhost", port=15672, transport=httpx.MockTransport(handler)) as adapter:
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

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(201)

    with HttpxAdapter(host="localhost", port=15672, transport=httpx.MockTransport(handler)) as adapter:
        QueueManager(adapter, "%2F", strict).create("configured-queue", QueueRequest(arguments=arguments))

    wire_arguments = sent[0]["arguments"]
    assert isinstance(wire_arguments, dict)
    assert wire_arguments["x-queue-type"] == arguments.queue_type
    assert wire_arguments[wire_key] == wire_value
