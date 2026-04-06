from typing import TYPE_CHECKING

from rabbitmq_management_sdk.http_adapter.config import BackoffStrategy, ExponentialBackoffWithJitter, TimeoutConfig
from rabbitmq_management_sdk.http_adapter.httpx import HttpxAdapter
from rabbitmq_management_sdk.http_adapter.retry import RetryTransport

if TYPE_CHECKING:
    import ssl

    from rabbitmq_management_sdk.http_adapter.base import HttpAdapter


def create_adapter(
    host: str,
    *,
    port: int = 15672,
    timeout: TimeoutConfig | None = None,
    default_headers: dict[str, str] | None = None,
    ssl_context: ssl.SSLContext | None = None,
    max_redirects: int = 5,
    max_retries: int = 3,
    backoff_strategy: BackoffStrategy | None = None,
) -> HttpAdapter:
    transport = HttpxAdapter(
        host=host,
        port=port,
        timeout=timeout,
        default_headers=default_headers,
        ssl_context=ssl_context,
        max_redirects=max_redirects,
    )

    return (
        RetryTransport(
            transport, backoff_strategy=backoff_strategy or ExponentialBackoffWithJitter(), max_attempts=max_retries
        )
        if max_retries > 0
        else transport
    )
