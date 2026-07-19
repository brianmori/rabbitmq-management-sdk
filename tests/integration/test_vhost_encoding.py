"""Integration tests verifying that vhost strings are correctly URL-encoded in API paths.

The encoding boundary is Config.virtual_host_safe: it encodes once with
quote(vhost, safe="") and passes the result to managers. Managers inject it
directly into path strings without re-encoding.
"""

from __future__ import annotations

from urllib.parse import quote

import httpx
import pytest

from rabbitmq_management_sdk.http_adapter.httpx import HttpxAdapter
from rabbitmq_management_sdk.resources.v4.bindings.services import BindingManagerV4
from rabbitmq_management_sdk.resources.v4.exchanges.services import ExchangeManagerV4
from rabbitmq_management_sdk.resources.v4.queues.services import QueueManagerV4


def _adapter_with_path_capture(seen_urls: list[str], *, response_body: bytes = b"{}") -> HttpxAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        # str(request.url) preserves percent-encoding (e.g. %2F stays %2F).
        # request.url.path decodes it back to the original character — avoid that.
        seen_urls.append(str(request.url))
        return httpx.Response(200, content=response_body)

    return HttpxAdapter(host="localhost", port=15672, transport=httpx.MockTransport(handler))


_VHOST_CASES = [
    ("/", "%2F"),
    ("my-vhost", "my-vhost"),
    ("a/b", "a%2Fb"),
    ("vhost with spaces", "vhost%20with%20spaces"),
]


@pytest.mark.integration
@pytest.mark.parametrize("raw,encoded", _VHOST_CASES)
def test_queue_path_contains_encoded_vhost(raw: str, encoded: str) -> None:
    seen: list[str] = []
    # Use DELETE (returns None, no response parsing) so the mock body doesn't matter
    adapter = _adapter_with_path_capture(seen)
    manager = QueueManagerV4(http_client=adapter, vhost=quote(raw, safe=""), strict=False)

    manager.delete("test-queue")

    assert len(seen) == 1
    assert f"/{encoded}/test-queue" in seen[0]


@pytest.mark.integration
@pytest.mark.parametrize("raw,encoded", _VHOST_CASES)
def test_exchange_path_contains_encoded_vhost(raw: str, encoded: str) -> None:
    seen: list[str] = []
    adapter = _adapter_with_path_capture(seen)
    manager = ExchangeManagerV4(http_client=adapter, vhost=quote(raw, safe=""), strict=False)

    manager.delete("amq.direct")

    assert len(seen) == 1
    assert f"/{encoded}/amq.direct" in seen[0]


@pytest.mark.integration
@pytest.mark.parametrize("raw,encoded", _VHOST_CASES)
def test_binding_list_path_contains_encoded_vhost(raw: str, encoded: str) -> None:
    seen: list[str] = []
    # list_by_vhost parses a list — return "[]" so validation succeeds
    adapter = _adapter_with_path_capture(seen, response_body=b"[]")
    manager = BindingManagerV4(http_client=adapter, vhost=quote(raw, safe=""), strict=False)

    manager.list_by_vhost()

    assert len(seen) == 1
    assert f"/bindings/{encoded}" in seen[0]


@pytest.mark.integration
def test_default_vhost_slash_encodes_to_percent_2f() -> None:
    """Regression: the default '/' vhost must become '%2F', not empty or literal '/'."""
    seen: list[str] = []
    adapter = _adapter_with_path_capture(seen)
    vhost_safe = quote("/", safe="")
    manager = QueueManagerV4(http_client=adapter, vhost=vhost_safe, strict=False)

    manager.delete("any-queue")

    assert "%2F" in seen[0]
    assert seen[0].count("/api/queues/%2F/any-queue") == 1


@pytest.mark.integration
def test_no_double_encoding() -> None:
    """Encoding '%2F' again must NOT produce '%252F'."""
    raw = "/"
    once = quote(raw, safe="")  # "%2F"

    seen: list[str] = []
    adapter = _adapter_with_path_capture(seen)
    manager = QueueManagerV4(http_client=adapter, vhost=once, strict=False)
    manager.delete("q")

    assert "%252F" not in seen[0], "Double-encoding detected — vhost was encoded twice"
    assert "%2F" in seen[0]
