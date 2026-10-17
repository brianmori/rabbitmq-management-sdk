"""Client initialization, authentication headers, and owned-adapter lifetime."""

from __future__ import annotations

import base64
from unittest.mock import patch

import httpx2
import pytest

from rabbitmq_management_sdk import Config, MalformedResponseError, RabbitMQClient, RabbitMQVersion, TransportError
from rabbitmq_management_sdk.exceptions import ConnectionError
from rabbitmq_management_sdk.http_adapter.base import HttpResponse
from rabbitmq_management_sdk.http_adapter.httpx2 import Httpx2Adapter

pytestmark = pytest.mark.unit


class _Adapter:
    def __init__(self, body: bytes = b'{"rabbitmq_version":"4.3.4"}') -> None:
        self.body = body
        self.requests = 0
        self.closes = 0
        self.request_error: Exception | None = None
        self.close_error: Exception | None = None

    def request(self, *, method: str, path: str) -> HttpResponse:
        self.requests += 1
        if self.request_error is not None:
            raise self.request_error
        return HttpResponse(200, {}, self.body)

    def close(self) -> None:
        self.closes += 1
        if self.close_error is not None:
            raise self.close_error


def _config(**overrides: object) -> Config:
    return Config.model_validate({"host": "localhost", "username": "user", "password": "password"} | overrides)


def test_context_manager_closes_adapter_once() -> None:
    adapter = _Adapter()
    with patch("rabbitmq_management_sdk.client.rabbitmq_client.factory.create_adapter", return_value=adapter):
        with RabbitMQClient(_config()) as client:
            assert str(client.version) == "4.3.4"
            assert adapter.closes == 0
        client.close()

    assert adapter.requests == 1
    assert adapter.closes == 1
    with pytest.raises(TransportError, match="closed"), client:
        pytest.fail("a closed client must not be entered")


def test_context_manager_preserves_body_exception_and_closes() -> None:
    adapter = _Adapter()
    with (
        patch("rabbitmq_management_sdk.client.rabbitmq_client.factory.create_adapter", return_value=adapter),
        pytest.raises(ValueError, match="application failure"),
        RabbitMQClient(_config()),
    ):
        raise ValueError("application failure")
    assert adapter.closes == 1


@pytest.mark.parametrize(
    "body",
    [b"null", b"[]", b'"version"', b"true", b"42", b"{}", b'{"rabbitmq_version":4}', b'{"rabbitmq_version":"invalid"}'],
)
def test_invalid_overview_raises_sdk_error_and_closes(body: bytes) -> None:
    adapter = _Adapter(body)
    with (
        patch("rabbitmq_management_sdk.client.rabbitmq_client.factory.create_adapter", return_value=adapter),
        pytest.raises(MalformedResponseError),
    ):
        RabbitMQClient(_config())
    assert adapter.closes == 1


def test_failed_discovery_preserves_error_when_cleanup_also_fails() -> None:
    adapter = _Adapter()
    original = ConnectionError("broker unavailable")
    adapter.request_error = original
    adapter.close_error = RuntimeError("cleanup failure")
    with (
        patch("rabbitmq_management_sdk.client.rabbitmq_client.factory.create_adapter", return_value=adapter),
        pytest.raises(ConnectionError) as error,
    ):
        RabbitMQClient(_config())
    assert error.value is original
    assert adapter.closes == 1


def test_version_override_does_not_request_overview() -> None:
    adapter = _Adapter(b"null")
    with (
        patch("rabbitmq_management_sdk.client.rabbitmq_client.factory.create_adapter", return_value=adapter),
        RabbitMQClient(_config(version_override=RabbitMQVersion.parse("4.2.6"))) as client,
    ):
        assert str(client.version) == "4.2.6"
    assert adapter.requests == 0
    assert adapter.closes == 1


@pytest.mark.parametrize("username,password", [("usér", "päss秘密"), ("user", "")])
def test_basic_auth_sends_utf8_credentials_including_empty_password(username: str, password: str) -> None:
    adapter = _Adapter()
    with (
        patch("rabbitmq_management_sdk.client.rabbitmq_client.factory.create_adapter", return_value=adapter) as create,
        RabbitMQClient(_config(username=username, password=password)),
    ):
        authorization = create.call_args.kwargs["default_headers"]["Authorization"]
    scheme, token = authorization.split(" ", 1)
    assert scheme == "Basic"
    assert base64.b64decode(token).decode("utf-8") == f"{username}:{password}"


def test_cached_manager_raises_sdk_error_after_client_closes() -> None:
    adapter = Httpx2Adapter(
        host="localhost",
        port=15672,
        transport=httpx2.MockTransport(lambda request: httpx2.Response(200, json={"rabbitmq_version": "4.3.4"})),
    )
    with (
        patch("rabbitmq_management_sdk.client.rabbitmq_client.factory.create_adapter", return_value=adapter),
        RabbitMQClient(_config()) as client,
    ):
        queues = client.queues
    with pytest.raises(TransportError, match="closed"):
        queues.delete("queue")
