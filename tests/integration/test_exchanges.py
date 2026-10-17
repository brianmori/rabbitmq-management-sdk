"""Exchange declaration wire keys and backwards-compatible validation."""

import json

import httpx2
import pytest

from rabbitmq_management_sdk import ExchangeRequest, ExchangeResponse
from rabbitmq_management_sdk.http_adapter.httpx2 import Httpx2Adapter
from rabbitmq_management_sdk.resources.v4.exchanges.services import ExchangeManager

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("key", ["auto_delete", "auto-delete"])
@pytest.mark.parametrize("auto_delete", [False, True])
def test_exchange_declaration_sends_underscored_auto_delete(key: str, auto_delete: bool) -> None:
    sent: list[dict[str, object]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        return httpx2.Response(204)

    declaration = ExchangeRequest.model_validate({key: auto_delete})
    with Httpx2Adapter("localhost", port=15672, transport=httpx2.MockTransport(handler)) as adapter:
        ExchangeManager(adapter, "%2F", False).create("exchange", declaration)

    assert sent[0]["auto_delete"] is auto_delete
    assert "auto-delete" not in sent[0]


@pytest.mark.parametrize("key", ["auto_delete", "auto-delete"])
def test_exchange_response_serializes_broker_wire_key(key: str) -> None:
    response = ExchangeResponse.model_validate(
        {
            "name": "exchange",
            "vhost": "/",
            "type": "direct",
            "durable": True,
            "internal": False,
            "arguments": {},
            key: True,
        }
    )
    payload = response.model_dump(by_alias=True)
    assert response.auto_delete is True
    assert payload["auto_delete"] is True
    assert "auto-delete" not in payload
