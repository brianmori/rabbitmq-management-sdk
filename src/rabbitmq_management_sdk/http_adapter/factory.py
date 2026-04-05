import ssl
from typing import TYPE_CHECKING

from rabbitmq_management_sdk.http_adapter.config import BackoffStrategy, ExponentialBackoffWithJitter, TimeoutConfig
from rabbitmq_management_sdk.http_adapter.httpx import HttpxAdapter
from rabbitmq_management_sdk.http_adapter.retry import RetryTransport

if TYPE_CHECKING:
    from rabbitmq_management_sdk.http_adapter.base import HttpAdapter


def create_adapter(
        base_url: str,
        *,
        timeout: TimeoutConfig | None = None,
        default_headers: dict[str, str] | None = None,
        ssl_context: ssl.SSLContext | None = None,
        max_redirects: int = 5,
        max_retries: int = 0,
        backoff_strategy: BackoffStrategy | None = None,
) -> HttpAdapter:
    transport = HttpxAdapter(
        base_url=base_url,
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


def create_ssl_context(
        *,
        ca_bundle: str | None = None,
        client_cert: tuple[str, str] | None = None,  # (cert_path, key_path)
        verify_ssl: bool = True,
) -> ssl.SSLContext:
    """
    Build an SSLContext for internal PKI or mutual TLS.

    Args:
        ca_bundle: Path to a custom CA bundle (.pem) for internal PKI.
        client_cert: Tuple of (cert_path, key_path) for mTLS.
        verify_ssl: Set to False to disable verification entirely (dev only).
    """

    ctx = ssl.create_default_context(cafile=ca_bundle)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = True

    if not verify_ssl:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    if client_cert:
        ctx.load_cert_chain(*client_cert)

    return ctx
