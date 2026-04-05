"""Abstract HTTP http_adapter protocol."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from types import TracebackType


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes


@runtime_checkable
class HttpAdapter(Protocol):
    """Protocol defining the HTTP http_adapter interface."""

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
