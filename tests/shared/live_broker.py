"""Bounded polling and HTTP message helpers for live tests.

Provision through SDK managers when testing them. Publishing and getting
messages are test-only HTTP operations, outside the SDK resource API.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import quote

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

    from rabbitmq_management_sdk.http_adapter import HttpAdapter


def wait_for[T](callback: Callable[[], T], label: str, *, timeout: float = 20) -> T:
    deadline = time.monotonic() + timeout
    while True:
        result = callback()
        if result:
            return result
        if time.monotonic() >= deadline:
            pytest.fail(f"Timed out waiting for {label}")
        time.sleep(0.25)


@dataclass
class ManagementAPI:
    adapter: HttpAdapter = field(repr=False)

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> object:
        response = self.adapter.request(method=method, path=f"/api/{path}", json=body)
        return response.json() if response.body else None

    def publish(
        self, vhost: str, exchange: str, key: str, payload: str, *, headers: dict[str, str] | None = None
    ) -> bool:
        response = self.request(
            "POST",
            f"exchanges/{quote(vhost, safe='')}/{quote(exchange, safe='')}/publish",
            {
                "routing_key": key,
                "properties": {"headers": headers or {}},
                "payload": payload,
                "payload_encoding": "string",
            },
        )
        assert isinstance(response, dict)
        return bool(response["routed"])

    def messages(self, vhost: str, queue: str, *, ackmode: str = "ack_requeue_false") -> list[dict[str, Any]]:
        response = self.request(
            "POST",
            f"queues/{quote(vhost, safe='')}/{quote(queue, safe='')}/get",
            {"count": 10, "ackmode": ackmode, "encoding": "auto"},
        )
        assert isinstance(response, list)
        return cast("list[dict[str, Any]]", response)

    def delivered(self, vhost: str, queue: str, payload: str) -> dict[str, Any]:
        messages = wait_for(lambda: self.messages(vhost, queue), f"delivery to {vhost}/{queue}")
        assert [message["payload"] for message in messages] == [payload]
        return messages[0]
