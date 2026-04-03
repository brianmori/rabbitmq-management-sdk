import httpx
import pytest

from rabbitmq_management_sdk.transport.httpx import HttpxAdapter


@pytest.mark.integration
def test_create_queue_success():
    # 1. Setup Mock Handler
    def handler(request):
        return httpx.Response(201, json={"status": "created"}, headers={"Content-Type": "application/json"})

    # 2. Inject MockTransport into your Adapter
    mock_transport = httpx.MockTransport(handler)
    adapter = HttpxAdapter(base_url="http://localhost:15672", transport=mock_transport)

    # 3. Test the high-level request
    response = adapter.request("PUT", "/api/queues/%2f/my-queue", json={"durable": True})

    assert response.status_code == 201
    assert response.headers["Content-Type"] == "application/json"