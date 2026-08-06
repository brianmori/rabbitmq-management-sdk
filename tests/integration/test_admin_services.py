"""Integration tests for AdminManager against a mocked HTTP transport."""

from __future__ import annotations

import httpx
import pytest

from rabbitmq_management_sdk.http_adapter.httpx import HttpxAdapter
from rabbitmq_management_sdk.resources.v4.admin.services import AdminManager


def _adapter_returning(body: bytes, status: int = 200) -> HttpxAdapter:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=body)

    return HttpxAdapter(host="localhost", port=15672, transport=httpx.MockTransport(handler))


@pytest.mark.integration
def test_get_vhost_limits_empty_returns_none() -> None:
    """Regression: an empty limits list must yield None, not leak IndexError.

    ``GET /api/vhost-limits/{vhost}`` returns ``[]`` for a vhost with no limits.
    The previous ``.pop()`` implementation raised a bare ``IndexError`` that
    escaped the SDK's ``RabbitMQError`` boundary.
    """
    manager = AdminManager(http_client=_adapter_returning(b"[]"), strict=False)

    assert manager.get_vhost_limits("missing") is None


@pytest.mark.integration
def test_get_vhost_limits_returns_single_entry() -> None:
    body = b'[{"vhost": "test", "value": {"max-connections": 3, "max-queues": 5}}]'
    manager = AdminManager(http_client=_adapter_returning(body), strict=False)

    result = manager.get_vhost_limits("test")

    assert result is not None
    assert result.vhost == "test"
    assert result.value.max_connections == 3
    assert result.value.max_queues == 5
