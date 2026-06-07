from __future__ import annotations

import builtins
import json


class RabbitMQError(Exception):
    """Base class for every error raised by the SDK."""


class TransportError(RabbitMQError):
    """Network or transport-level failure with no HTTP response."""


class TimeoutError(TransportError, builtins.TimeoutError):
    """Connect, read, write, or pool timeout."""


class ConnectionError(TransportError, builtins.ConnectionError):
    """DNS failure, connection refused, broken pipe, TLS handshake failure."""


class APIError(RabbitMQError):
    """HTTP response received with a status code >= 400."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        method: str,
        path: str,
        response_body: bytes | None = None,
        error: str | None = None,
        reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.method = method
        self.path = path
        self.response_body = response_body
        self.error = error
        self.reason = reason

    def __str__(self) -> str:
        base = super().__str__()
        if self.reason:
            return f"{base} — {self.reason}"
        return base


class BadRequestError(APIError):
    """HTTP 400."""


class UnauthorizedError(APIError):
    """HTTP 401."""


class ForbiddenError(APIError):
    """HTTP 403."""


class NotFoundError(APIError):
    """HTTP 404."""


class MethodNotAllowedError(APIError):
    """HTTP 405."""


class ConflictError(APIError):
    """HTTP 409 — resource already exists with conflicting properties."""


class PreconditionFailedError(APIError):
    """HTTP 412 — common in RabbitMQ for in-use or not-empty resources."""


class UnprocessableEntityError(APIError):
    """HTTP 422."""


class TooManyRequestsError(APIError):
    """HTTP 429 — rate limited. Honour Retry-After when retrying."""


class ServerError(APIError):
    """HTTP 5xx — broker-side error."""


class ServiceUnavailableError(ServerError):
    """HTTP 503."""


class MalformedResponseError(RabbitMQError):
    """Broker returned a successful status but an unexpected response shape.

    Example: a binding creation responded 201 with no Location header.
    """


_STATUS_TO_EXCEPTION: dict[int, type[APIError]] = {
    400: BadRequestError,
    401: UnauthorizedError,
    403: ForbiddenError,
    404: NotFoundError,
    405: MethodNotAllowedError,
    409: ConflictError,
    412: PreconditionFailedError,
    422: UnprocessableEntityError,
    429: TooManyRequestsError,
    503: ServiceUnavailableError,
}


_HTTP_5XX_MIN: int = 500
_HTTP_5XX_MAX: int = 600


def _exception_class_for(status_code: int) -> type[APIError]:
    if status_code in _STATUS_TO_EXCEPTION:
        return _STATUS_TO_EXCEPTION[status_code]
    if _HTTP_5XX_MIN <= status_code < _HTTP_5XX_MAX:
        return ServerError
    return APIError


def _parse_rabbitmq_body(body: bytes | None) -> tuple[str | None, str | None]:
    """Best-effort extraction of ``(error, reason)`` from RabbitMQ's JSON body.

    RabbitMQ returns JSON like ``{"error": "bad_request", "reason": "..."}`` on
    most management API failures. Anything that doesn't match that shape (empty
    body, non-JSON payload, unexpected types) yields ``(None, None)``.
    """
    if not body:
        return None, None
    try:
        data = json.loads(body)
    except json.JSONDecodeError, UnicodeDecodeError:
        return None, None
    if not isinstance(data, dict):
        return None, None
    error = data.get("error")
    reason = data.get("reason")
    return (
        error if isinstance(error, str) else None,
        reason if isinstance(reason, str) else None,
    )


def api_error_from_response(
    *,
    status_code: int,
    method: str,
    path: str,
    body: bytes | None,
) -> APIError:
    """Build the correct :class:`APIError` subclass from an HTTP error response."""
    error, reason = _parse_rabbitmq_body(body)
    cls = _exception_class_for(status_code)
    message = f"HTTP {status_code} {method} {path}"
    return cls(
        message,
        status_code=status_code,
        method=method,
        path=path,
        response_body=body,
        error=error,
        reason=reason,
    )
