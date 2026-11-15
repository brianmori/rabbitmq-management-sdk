"""Configured raw vhost names are encoded once at the manager boundary."""

from __future__ import annotations

import httpx2
import pytest

from rabbitmq_management_sdk import Config
from rabbitmq_management_sdk.http_adapter.httpx2 import Httpx2Adapter
from rabbitmq_management_sdk.resources.v4.bindings.services import BindingManager
from rabbitmq_management_sdk.resources.v4.exchanges.services import ExchangeManager
from rabbitmq_management_sdk.resources.v4.queues.services import QueueManager

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    ("raw", "encoded"),
    [
        ("/", "%2F"),
        ("my-vhost", "my-vhost"),
        ("a/b", "a%2Fb"),
        ("vhost with spaces", "vhost%20with%20spaces"),
        ("%2F", "%252F"),
    ],
)
@pytest.mark.parametrize("resource", ["queues", "exchanges", "bindings"])
def test_configured_vhost_is_encoded_once_in_manager_path(raw: str, encoded: str, resource: str) -> None:
    config = Config(host="localhost", username="fixture", password="fixture", virtual_host=raw)
    assert config.virtual_host_safe == encoded
    seen: list[tuple[str, bytes]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append((request.method, request.url.raw_path))
        return httpx2.Response(200, json=[])

    with Httpx2Adapter(host="localhost", port=15672, transport=httpx2.MockTransport(handler)) as adapter:
        if resource == "bindings":
            assert BindingManager(adapter, config.virtual_host_safe, False).list_by_vhost() == []
        elif resource == "queues":
            QueueManager(adapter, config.virtual_host_safe, False).delete("fixture")
        else:
            ExchangeManager(adapter, config.virtual_host_safe, False).delete("fixture")
    method, suffix = ("GET", "") if resource == "bindings" else ("DELETE", "/fixture")
    assert seen == [(method, f"/api/{resource}/{encoded}{suffix}".encode())]
