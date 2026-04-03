"""Abstract HTTP transport protocol."""

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes

@runtime_checkable
class HttpAdapter(Protocol):
    """Protocol defining the HTTP transport interface."""

    def request(
            self,
            *,
            method: str,
            path: str,
            params: dict[str, Any] | None = None,
            json: dict[str, Any] | None = None,
            headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        """Execute an HTTP request."""

    def __enter__(self) -> HttpAdapter: ...

    def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc_val: BaseException | None,
            exc_tb: TracebackType | None,
    ) -> None: ...

    def close(self) -> None: ...
