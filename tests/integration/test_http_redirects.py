"""HTTPX2 redirects preserve encoded paths and limit credential forwarding."""

from __future__ import annotations

import httpx2
import pytest

from rabbitmq_management_sdk.http_adapter.httpx2 import Httpx2Adapter

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("redirect_host", ["localhost", "other.example"])
def test_redirect_preserves_encoded_path_and_scopes_authorization(redirect_host: str) -> None:
    seen: list[httpx2.Request] = []
    path = "/api/queues/%2F/orders%2Feu%3F%25"
    authorization = "Basic fixture-token"

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        if len(seen) == 1:
            return httpx2.Response(302, headers={"Location": f"http://{redirect_host}:15672{path}"})
        return httpx2.Response(200, json={"name": "orders/eu?%"})

    with Httpx2Adapter(
        host="localhost",
        port=15672,
        default_headers={"Authorization": authorization},
        transport=httpx2.MockTransport(handler),
    ) as adapter:
        response = adapter.request(method="GET", path="/api/redirect")

    assert response.json() == {"name": "orders/eu?%"}
    assert len(seen) == 2
    assert seen[1].url.raw_path == path.encode("ascii")
    assert seen[0].headers["Authorization"] == authorization
    expected = authorization if redirect_host == "localhost" else None
    assert seen[1].headers.get("Authorization") == expected
