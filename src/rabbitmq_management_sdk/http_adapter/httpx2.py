from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx2

from rabbitmq_management_sdk.exceptions import (
    ConnectionError as RMQConnectionError,
)
from rabbitmq_management_sdk.exceptions import (
    TimeoutError as RMQTimeoutError,
)
from rabbitmq_management_sdk.exceptions import (
    TransportError,
    api_error_from_response,
)
from rabbitmq_management_sdk.http_adapter.base import HttpResponse
from rabbitmq_management_sdk.http_adapter.config import TimeoutConfig

if TYPE_CHECKING:
    import ssl
    from types import TracebackType


class Httpx2Adapter:
    """HTTP adapter implementation using HTTPX2.

    Custom transports must use HTTPX2 types. Retries are provided by the factory.
    Use as a context manager or call .close() explicitly.
    """

    def __init__(
        self,
        host: str,
        *,
        port: int,
        timeout: TimeoutConfig | None = None,
        default_headers: dict[str, str] | None = None,
        ssl_context: ssl.SSLContext | None = None,
        max_redirects: int = 5,
        transport: httpx2.BaseTransport | None = None,
    ) -> None:

        scheme = "http"
        if ssl_context:
            scheme = "https"
        base_url = f"{scheme}://{host}:{port}"

        to = timeout or TimeoutConfig()
        self._client = httpx2.Client(
            base_url=base_url,
            timeout=httpx2.Timeout(
                connect=to.connect,
                read=to.read,
                write=to.write,
                pool=to.pool,
            ),
            headers=default_headers or {},
            verify=ssl_context or True,
            max_redirects=max_redirects,
            follow_redirects=True,
            transport=transport,
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:

        if self._client.is_closed:
            raise TransportError("HTTP adapter is closed")
        try:
            response = self._client.request(
                method=method.upper(),
                url=path,
                params=params,
                json=json,
                headers=headers,
            )
            response.raise_for_status()
            return HttpResponse(
                status_code=response.status_code,
                headers=response.headers,
                body=response.content,
            )
        except httpx2.HTTPStatusError as e:
            raise api_error_from_response(
                status_code=e.response.status_code,
                method=method.upper(),
                path=path,
                body=e.response.content,
            ) from e
        except httpx2.TimeoutException as e:
            raise RMQTimeoutError(f"Request timed out: {method.upper()} {path}") from e
        except httpx2.NetworkError as e:
            raise RMQConnectionError(f"Network error: {method.upper()} {path}") from e
        except httpx2.HTTPError as e:
            raise TransportError(f"HTTP error: {method.upper()} {path}") from e

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Httpx2Adapter:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()
