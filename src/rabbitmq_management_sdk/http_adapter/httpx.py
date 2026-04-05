from typing import TYPE_CHECKING, Any

import httpx

from rabbitmq_management_sdk.http_adapter import (
    HttpResponse,
    TransportConnectionError,
    TransportResponseError,
    TransportTimeoutError,
)
from rabbitmq_management_sdk.http_adapter.config import TimeoutConfig

if TYPE_CHECKING:
    import ssl
    from types import TracebackType


class HttpxAdapter:
    """
    HttpTransport implementation using httpx.

    Supports base URL, default headers, timeouts, and retries.
    Use as a context manager or call .close() explicitly.
    """

    def __init__(
            self,
            base_url: str,
            *,
            timeout: TimeoutConfig | None = None,
            default_headers: dict[str, str] | None = None,
            ssl_context: ssl.SSLContext | None = None,
            max_redirects: int = 5,
            transport: httpx.BaseTransport | None = None,
    ) -> None:

        to = timeout or TimeoutConfig()
        self._client = httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(
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
        except httpx.HTTPStatusError as e:
            raise TransportResponseError(
                f"HTTP {e.response.status_code}: {method.upper()} {path}",
                status_code=e.response.status_code,
                response_body=e.response.content,
            ) from e
        except httpx.TimeoutException as e:
            raise TransportTimeoutError(f"Request timed out: {method.upper()} {path}") from e
        except httpx.NetworkError as e:
            raise TransportConnectionError(f"Network error: {method.upper()} {path}") from e
        except httpx.HTTPError as e:
            raise TransportResponseError(f"HTTP error: {method.upper()} {path}") from e

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HttpxAdapter:
        return self

    def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc_val: BaseException | None,
            exc_tb: TracebackType | None,
    ) -> None:
        self.close()
