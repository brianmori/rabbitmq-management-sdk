"""Integration tests verifying that no third-party exceptions leak from the adapter layer.

Every exception a caller can observe must be a subclass of RabbitMQError.
httpx, json, and pydantic types must not escape.
"""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import ValidationError

import rabbitmq_management_sdk as sdk
from rabbitmq_management_sdk.exceptions import (
    APIError,
    BadRequestError,
    ConflictError,
    ConnectionError,
    ForbiddenError,
    MalformedResponseError,
    MethodNotAllowedError,
    NotFoundError,
    PreconditionFailedError,
    RabbitMQError,
    ServerError,
    ServiceUnavailableError,
    TimeoutError,
    TooManyRequestsError,
    UnauthorizedError,
    UnprocessableEntityError,
)
from rabbitmq_management_sdk.http_adapter.base import HttpResponse
from rabbitmq_management_sdk.http_adapter.httpx import HttpxAdapter
from rabbitmq_management_sdk.resources.base import RabbitMQBase, parse_list, parse_one


def _adapter_for(handler: httpx.MockTransport | None = None, **kw: object) -> HttpxAdapter:
    transport = handler or httpx.MockTransport(lambda r: httpx.Response(200))
    return HttpxAdapter(host="localhost", port=15672, transport=transport, **kw)  # type: ignore[arg-type]


def _error_body(error: str, reason: str) -> bytes:
    return json.dumps({"error": error, "reason": reason}).encode()


# ---------------------------------------------------------------------------
# HTTP status → exception class mapping
# ---------------------------------------------------------------------------

_STATUS_CASES: list[tuple[int, type[APIError]]] = [
    (400, BadRequestError),
    (401, UnauthorizedError),
    (403, ForbiddenError),
    (404, NotFoundError),
    (405, MethodNotAllowedError),
    (409, ConflictError),
    (412, PreconditionFailedError),
    (422, UnprocessableEntityError),
    (429, TooManyRequestsError),
    (500, ServerError),
    (501, ServerError),
    (503, ServiceUnavailableError),
]


@pytest.mark.integration
@pytest.mark.parametrize("status_code,expected_cls", _STATUS_CASES)
def test_http_status_raises_correct_exception(status_code: int, expected_cls: type[APIError]) -> None:
    body = _error_body("test_error", "test reason")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, content=body)

    adapter = _adapter_for(httpx.MockTransport(handler))

    with pytest.raises(expected_cls) as exc_info:
        adapter.request(method="GET", path="/api/test")

    exc = exc_info.value
    assert exc.status_code == status_code
    assert exc.method == "GET"
    assert exc.path == "/api/test"
    assert exc.error == "test_error"
    assert exc.reason == "test reason"
    assert isinstance(exc.__cause__, httpx.HTTPStatusError)


@pytest.mark.integration
@pytest.mark.parametrize("status_code,expected_cls", _STATUS_CASES)
def test_http_error_is_rabbitmq_error(status_code: int, expected_cls: type[APIError]) -> None:
    """Every HTTP error must be catchable as RabbitMQError."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, content=b"{}")

    adapter = _adapter_for(httpx.MockTransport(handler))

    with pytest.raises(RabbitMQError):
        adapter.request(method="GET", path="/api/test")


@pytest.mark.integration
@pytest.mark.parametrize("status_code,expected_cls", _STATUS_CASES)
def test_no_httpx_exception_leaks(status_code: int, expected_cls: type[APIError]) -> None:
    """Httpx exception types must never reach the caller."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, content=b"{}")

    adapter = _adapter_for(httpx.MockTransport(handler))

    with pytest.raises(RabbitMQError) as exc_info:
        adapter.request(method="GET", path="/api/test")

    assert not isinstance(exc_info.value, httpx.HTTPError)


# ---------------------------------------------------------------------------
# Transport-level failures (timeout, connection)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_timeout_raises_sdk_timeout_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    adapter = _adapter_for(httpx.MockTransport(handler))

    with pytest.raises(TimeoutError) as exc_info:
        adapter.request(method="GET", path="/api/test")

    assert isinstance(exc_info.value.__cause__, httpx.TimeoutException)
    assert not isinstance(exc_info.value, httpx.HTTPError)


@pytest.mark.integration
def test_connection_error_raises_sdk_connection_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    adapter = _adapter_for(httpx.MockTransport(handler))

    with pytest.raises(ConnectionError) as exc_info:
        adapter.request(method="GET", path="/api/test")

    assert isinstance(exc_info.value.__cause__, httpx.NetworkError)
    assert not isinstance(exc_info.value, httpx.HTTPError)


@pytest.mark.integration
def test_timeout_is_rabbitmq_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    adapter = _adapter_for(httpx.MockTransport(handler))

    with pytest.raises(RabbitMQError):
        adapter.request(method="GET", path="/api/test")


# ---------------------------------------------------------------------------
# JSON decode errors → MalformedResponseError
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_malformed_json_body_raises_malformed_response_error() -> None:
    response = HttpResponse(status_code=200, headers={}, body=b"not json {{")

    with pytest.raises(MalformedResponseError) as exc_info:
        response.json()

    assert isinstance(exc_info.value.__cause__, (ValueError, UnicodeDecodeError))
    assert not isinstance(exc_info.value, (ValueError, UnicodeDecodeError))


@pytest.mark.integration
def test_empty_body_raises_malformed_response_error() -> None:
    response = HttpResponse(status_code=200, headers={}, body=b"")

    with pytest.raises(MalformedResponseError):
        response.json()


# ---------------------------------------------------------------------------
# Pydantic ValidationError → MalformedResponseError via parse helpers
# ---------------------------------------------------------------------------


class _SampleModel(RabbitMQBase):
    name: str
    value: int


@pytest.mark.integration
def test_parse_one_wrong_shape_raises_malformed_response_error() -> None:
    response = HttpResponse(status_code=200, headers={}, body=json.dumps({"wrong": "keys"}).encode())

    with pytest.raises(MalformedResponseError) as exc_info:
        parse_one(response, _SampleModel)

    assert isinstance(exc_info.value.__cause__, ValidationError)
    assert not isinstance(exc_info.value, ValidationError)


@pytest.mark.integration
def test_parse_list_wrong_item_shape_raises_malformed_response_error() -> None:
    body = json.dumps([{"name": "ok", "value": 1}, {"wrong": "item"}]).encode()
    response = HttpResponse(status_code=200, headers={}, body=body)

    with pytest.raises(MalformedResponseError) as exc_info:
        parse_list(response, _SampleModel)

    assert isinstance(exc_info.value.__cause__, ValidationError)


@pytest.mark.integration
def test_parse_one_non_json_raises_malformed_response_error() -> None:
    response = HttpResponse(status_code=200, headers={}, body=b"<html>error</html>")

    with pytest.raises(MalformedResponseError):
        parse_one(response, _SampleModel)


# ---------------------------------------------------------------------------
# Error body parsing — malformed bodies must not leak json/unicode exceptions
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize(
    "body",
    [
        pytest.param(b"not valid json {{", id="non-json-body"),
        pytest.param(b"\xff\xfe\xfd invalid utf-8", id="non-utf8-body"),
        pytest.param(b'"a bare string"', id="json-but-not-object"),
        pytest.param(b"[]", id="json-array-not-object"),
    ],
)
def test_malformed_error_body_does_not_leak(body: bytes) -> None:
    """API error responses with unparseable bodies must still raise APIError cleanly."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, content=body)

    adapter = _adapter_for(httpx.MockTransport(handler))

    with pytest.raises(BadRequestError) as exc_info:
        adapter.request(method="GET", path="/api/test")

    exc = exc_info.value
    assert exc.error is None
    assert exc.reason is None
    assert not isinstance(exc, (json.JSONDecodeError, UnicodeDecodeError, ValueError))


# ---------------------------------------------------------------------------
# Public API surface — exceptions importable from root package
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_exceptions_importable_from_root_package() -> None:
    assert sdk.RabbitMQError is RabbitMQError
    assert sdk.NotFoundError is NotFoundError
    assert sdk.ConflictError is ConflictError
    assert sdk.MalformedResponseError is MalformedResponseError
    assert sdk.TimeoutError is TimeoutError
    assert sdk.ConnectionError is ConnectionError
