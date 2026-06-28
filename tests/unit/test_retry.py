"""Unit tests for RetryTransport backoff, retryable classification, and exhaustion."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import TracebackType

from rabbitmq_management_sdk.exceptions import (
    ConnectionError,
    NotFoundError,
    RabbitMQError,
    TimeoutError,
)
from rabbitmq_management_sdk.http_adapter.base import HttpResponse
from rabbitmq_management_sdk.http_adapter.config import NoBackoff
from rabbitmq_management_sdk.http_adapter.retry import RetryTransport


class _CountingAdapter:
    """Minimal HttpAdapter stub that raises exc for the first fail_times calls."""

    def __init__(self, fail_times: int = 0, exc: RabbitMQError | None = None) -> None:
        self.calls = 0
        self._fail_times = fail_times
        self._exc = exc or TimeoutError("transient")

    def request(
        self,
        *,
        method: str,
        path: str,
        params: object = None,
        json: object = None,
        headers: object = None,
    ) -> HttpResponse:
        self.calls += 1
        if self.calls <= self._fail_times:
            raise self._exc
        return HttpResponse(status_code=200, headers={}, body=b"{}")

    def close(self) -> None:
        pass

    def __enter__(self) -> _CountingAdapter:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        pass


def _retry(adapter: _CountingAdapter, **kw: object) -> RetryTransport:
    return RetryTransport(adapter, backoff_strategy=NoBackoff(), **kw)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Success paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_success_on_first_attempt() -> None:
    adapter = _CountingAdapter(fail_times=0)
    result = _retry(adapter).request(method="GET", path="/api/test")

    assert adapter.calls == 1
    assert result.status_code == 200


@pytest.mark.unit
def test_retry_on_timeout_succeeds_on_second_attempt() -> None:
    adapter = _CountingAdapter(fail_times=1, exc=TimeoutError("transient"))
    result = _retry(adapter, max_attempts=3).request(method="GET", path="/api/test")

    assert adapter.calls == 2
    assert result.status_code == 200


@pytest.mark.unit
def test_retry_on_connection_error_succeeds_on_third_attempt() -> None:
    adapter = _CountingAdapter(fail_times=2, exc=ConnectionError("refused"))
    result = _retry(adapter, max_attempts=3).request(method="GET", path="/api/test")

    assert adapter.calls == 3
    assert result.status_code == 200


# ---------------------------------------------------------------------------
# Exhaustion
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_exhausts_all_attempts_raises_last_exception() -> None:
    exc = TimeoutError("always fails")
    adapter = _CountingAdapter(fail_times=3, exc=exc)

    with pytest.raises(TimeoutError):
        _retry(adapter, max_attempts=3).request(method="GET", path="/api/test")

    assert adapter.calls == 3


@pytest.mark.unit
def test_max_attempts_one_no_retry() -> None:
    adapter = _CountingAdapter(fail_times=1, exc=TimeoutError("fail"))

    with pytest.raises(TimeoutError):
        _retry(adapter, max_attempts=1).request(method="GET", path="/api/test")

    assert adapter.calls == 1


# ---------------------------------------------------------------------------
# Non-retryable exceptions
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_non_retryable_exception_not_retried() -> None:
    exc = NotFoundError(
        "HTTP 404 GET /api/queues/%2F/q",
        status_code=404,
        method="GET",
        path="/api/queues/%2F/q",
    )
    adapter = _CountingAdapter(fail_times=3, exc=exc)

    with pytest.raises(NotFoundError):
        _retry(adapter, max_attempts=3).request(method="GET", path="/api/test")

    assert adapter.calls == 1, "Non-retryable exception should not trigger retries"


# ---------------------------------------------------------------------------
# Custom retryable_exceptions
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_custom_retryable_exceptions() -> None:
    exc = NotFoundError(
        "HTTP 404 GET /api/test",
        status_code=404,
        method="GET",
        path="/api/test",
    )
    adapter = _CountingAdapter(fail_times=1, exc=exc)

    # Explicitly make NotFoundError retryable
    result = _retry(adapter, max_attempts=3, retryable_exceptions=(NotFoundError,)).request(
        method="GET", path="/api/test"
    )

    assert adapter.calls == 2
    assert result.status_code == 200


# ---------------------------------------------------------------------------
# No backoff (timing sanity)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_no_backoff_completes_without_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """NoBackoff must never call sleep."""
    sleep_calls: list[float] = []

    def _record_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(time, "sleep", _record_sleep)

    adapter = _CountingAdapter(fail_times=2, exc=TimeoutError("transient"))
    _retry(adapter, max_attempts=3).request(method="GET", path="/api/test")

    assert sleep_calls == [], "NoBackoff should not invoke sleep"
