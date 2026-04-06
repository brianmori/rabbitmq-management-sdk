import httpx
import pytest

from rabbitmq_management_sdk.http_adapter.httpx import HttpxAdapter


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
