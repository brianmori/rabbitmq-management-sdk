from time import sleep
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from types import TracebackType

from rabbitmq_management_sdk.http_adapter import (
    HttpAdapter,
    HttpResponse,
    TransportConnectionError,
    TransportError,
    TransportTimeoutError,
)
from rabbitmq_management_sdk.http_adapter.config import BackoffStrategy, ExponentialBackoffWithJitter

_DEFAULT_RETRYABLE: tuple[type[TransportError], ...] = (
    TransportTimeoutError,
    TransportConnectionError,
)


class RetryTransport:
    def __init__(
            self,
            transport: HttpAdapter,
            *,
            max_attempts: int = 3,
            backoff_strategy: BackoffStrategy | None = None,
            retryable_exceptions: tuple[type[TransportError], ...] = _DEFAULT_RETRYABLE,
    ) -> None:
        self._transport = transport
        self._max_attempts = max_attempts
        self._backoff = backoff_strategy or ExponentialBackoffWithJitter()
        self._retryable_exceptions = retryable_exceptions

    def request(
            self,
            method: str,
            path: str,
            *,
            params: dict[str, Any] | None = None,
            json: dict[str, Any] | None = None,
            headers: dict[str, str] | None = None,
    ) -> HttpResponse:

        last_exc: TransportError | None = None
        for attempt in range(self._max_attempts):
            try:
                return self._transport.request(method=method, path=path, params=params, json=json, headers=headers)
            except self._retryable_exceptions as e:
                last_exc = e
                wait = self._backoff.wait_time(attempt)
                if wait > 0:
                    sleep(wait)

        raise last_exc

    def close(self) -> None:
        self._transport.close()

    def __enter__(self) -> RetryTransport:
        return self

    def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc_val: BaseException | None,
            exc_tb: TracebackType | None,
    ) -> None:
        self.close()
