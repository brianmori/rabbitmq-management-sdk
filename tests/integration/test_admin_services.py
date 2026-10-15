"""Integration tests for AdminManager against a mocked HTTP transport."""

from __future__ import annotations

import httpx
import pytest
from pydantic import ValidationError

from rabbitmq_management_sdk.exceptions import MalformedResponseError
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


@pytest.mark.integration
@pytest.mark.parametrize(
    "body",
    [
        pytest.param(b"{}", id="empty-object"),
        pytest.param(b'{"error": "not_authorised", "reason": "fixture"}', id="error-response"),
        pytest.param(b'{"rabbitmq_definition_format": "single_virtual_host", "queues": []}', id="single-vhost"),
        pytest.param(b'{"original_vhost_name": "/", "queues": []}', id="single-vhost-without-format"),
        pytest.param(b'{"rabbitmq_definition_format": "unknown", "queues": []}', id="incompatible-format"),
    ],
)
def test_export_definitions_wraps_invalid_envelopes(body: bytes) -> None:
    with _adapter_returning(body) as adapter:
        manager = AdminManager(http_client=adapter, strict=False)

        with pytest.raises(MalformedResponseError, match="ClusterDefinitionsResponse") as error:
            manager.export_definitions()

    assert isinstance(error.value.__cause__, ValidationError)


@pytest.mark.integration
def test_export_definitions_accepts_an_empty_legacy_cluster() -> None:
    with _adapter_returning(b'{"vhosts": [], "queues": [], "exchanges": [], "bindings": []}') as adapter:
        definitions = AdminManager(http_client=adapter, strict=False).export_definitions()

    assert definitions.rabbitmq_definition_format is None
    assert definitions.vhosts == []
    assert definitions.queues == []
