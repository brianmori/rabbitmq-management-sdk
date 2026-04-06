"""Abstract HTTP http_adapter protocol."""

import json as _json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, cast, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping
    from types import TracebackType


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes

    def json(self) -> dict[str, Any]:
        return cast("dict[str, Any]", _json.loads(self.body))


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
