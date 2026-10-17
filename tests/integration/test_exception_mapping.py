"""Integration tests verifying that no third-party exceptions leak from the adapter layer.

Every exception a caller can observe must be a subclass of RabbitMQError.
httpx2, json, and pydantic types must not escape.
"""

from __future__ import annotations

import json

import httpx2
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
    TransportError,
    UnauthorizedError,
    UnprocessableEntityError,
)
from rabbitmq_management_sdk.http_adapter.base import HttpResponse
from rabbitmq_management_sdk.http_adapter.httpx2 import Httpx2Adapter
from rabbitmq_management_sdk.resources.base import RabbitMQBase, parse_list, parse_one, parse_page


def _adapter_for(handler: httpx2.MockTransport | None = None, **kw: object) -> Httpx2Adapter:
    transport = handler or httpx2.MockTransport(lambda r: httpx2.Response(200))
    return Httpx2Adapter(host="localhost", port=15672, transport=transport, **kw)  # type: ignore[arg-type]


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

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status_code, content=body)

    adapter = _adapter_for(httpx2.MockTransport(handler))

    with pytest.raises(expected_cls) as exc_info:
        adapter.request(method="GET", path="/api/test")

    exc = exc_info.value
    assert exc.status_code == status_code
    assert exc.method == "GET"
    assert exc.path == "/api/test"
    assert exc.error == "test_error"
    assert exc.reason == "test reason"
    assert isinstance(exc.__cause__, httpx2.HTTPStatusError)


@pytest.mark.integration
@pytest.mark.parametrize("status_code,expected_cls", _STATUS_CASES)
def test_http_error_is_rabbitmq_error(status_code: int, expected_cls: type[APIError]) -> None:
    """Every HTTP error must be catchable as RabbitMQError."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status_code, content=b"{}")

    adapter = _adapter_for(httpx2.MockTransport(handler))

    with pytest.raises(RabbitMQError):
        adapter.request(method="GET", path="/api/test")


@pytest.mark.integration
@pytest.mark.parametrize("status_code,expected_cls", _STATUS_CASES)
def test_no_httpx2_exception_leaks(status_code: int, expected_cls: type[APIError]) -> None:
    """HTTPX2 exception types must never reach the caller."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status_code, content=b"{}")

    adapter = _adapter_for(httpx2.MockTransport(handler))

    with pytest.raises(RabbitMQError) as exc_info:
        adapter.request(method="GET", path="/api/test")

    assert not isinstance(exc_info.value, httpx2.HTTPError)


# ---------------------------------------------------------------------------
# Transport-level failures (timeout, connection)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_type", [httpx2.ConnectTimeout, httpx2.ReadTimeout, httpx2.WriteTimeout, httpx2.PoolTimeout]
)
def test_timeout_raises_sdk_timeout_error(error_type: type[httpx2.TimeoutException]) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise error_type("timed out")

    adapter = _adapter_for(httpx2.MockTransport(handler))

    with pytest.raises(TimeoutError) as exc_info:
        adapter.request(method="GET", path="/api/test")

    assert isinstance(exc_info.value.__cause__, httpx2.TimeoutException)
    assert not isinstance(exc_info.value, httpx2.HTTPError)


@pytest.mark.integration
@pytest.mark.parametrize("error_type", [httpx2.ConnectError, httpx2.ReadError, httpx2.WriteError, httpx2.CloseError])
def test_connection_error_raises_sdk_connection_error(error_type: type[httpx2.NetworkError]) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise error_type("network failure")

    adapter = _adapter_for(httpx2.MockTransport(handler))

    with pytest.raises(ConnectionError) as exc_info:
        adapter.request(method="GET", path="/api/test")

    assert isinstance(exc_info.value.__cause__, httpx2.NetworkError)
    assert not isinstance(exc_info.value, httpx2.HTTPError)


@pytest.mark.integration
@pytest.mark.parametrize("error_type", [httpx2.RemoteProtocolError, httpx2.UnsupportedProtocol, httpx2.DecodingError])
def test_other_http_failures_raise_sdk_transport_error(error_type: type[httpx2.HTTPError]) -> None:
    original = error_type("transport failure")

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise original

    with _adapter_for(httpx2.MockTransport(handler)) as adapter, pytest.raises(TransportError) as exc_info:
        adapter.request(method="GET", path="/api/test")

    assert exc_info.value.__cause__ is original


@pytest.mark.integration
def test_redirect_limit_raises_sdk_transport_error() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(302, headers={"Location": "/api/loop"})

    with (
        _adapter_for(httpx2.MockTransport(handler), max_redirects=1) as adapter,
        pytest.raises(TransportError) as exc_info,
    ):
        adapter.request(method="GET", path="/api/loop")

    assert isinstance(exc_info.value.__cause__, httpx2.TooManyRedirects)


@pytest.mark.integration
def test_timeout_is_rabbitmq_error() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectTimeout("timed out")

    adapter = _adapter_for(httpx2.MockTransport(handler))

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


@pytest.mark.integration
@pytest.mark.parametrize("body", [b"null", b"{}", b'"text"', b"true", b"42", b"1.5"])
def test_parse_list_rejects_non_array_json(body: bytes) -> None:
    with pytest.raises(MalformedResponseError, match="JSON array"):
        parse_list(HttpResponse(200, {}, body), _SampleModel)


@pytest.mark.integration
@pytest.mark.parametrize("body", [b"null", b"[]", b'"text"', b"true", b"42"])
def test_parse_object_and_page_reject_wrong_containers(body: bytes) -> None:
    response = HttpResponse(200, {}, body)
    with pytest.raises(MalformedResponseError):
        parse_one(response, _SampleModel)
    with pytest.raises(MalformedResponseError):
        parse_page(response, _SampleModel)


@pytest.mark.integration
def test_parse_list_accepts_empty_array() -> None:
    assert parse_list(HttpResponse(200, {}, b"[]"), _SampleModel) == []


@pytest.mark.integration
def test_http_json_returns_decoded_value_without_assuming_object() -> None:
    assert HttpResponse(200, {}, b"null").json() is None
    assert HttpResponse(200, {}, b"[]").json() == []
    assert HttpResponse(200, {}, b'{"key":1}').json() == {"key": 1}


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

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(400, content=body)

    adapter = _adapter_for(httpx2.MockTransport(handler))

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
