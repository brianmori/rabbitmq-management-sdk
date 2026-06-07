import ssl
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rabbitmq_management_sdk.client.config import SSLConfig


def create_ssl_context(sc: SSLConfig) -> ssl.SSLContext:
    """Creates a SSL context for the RabbitMQ Management API.

    TLS Version defaults to TLSv1.2 and hostname verification is enabled.

    Args:
        sc: SSL configuration for the client.

    Returns:
        An SSL context configured with the provided SSL settings.
    """
    ctx = ssl.create_default_context(
        purpose=ssl.Purpose.SERVER_AUTH,
        cafile=sc.ca_bundle,
    )
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.minimum_version = sc.min_version
    ctx.check_hostname = True

    if not sc.verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    if sc.client_cert:
        ctx.load_cert_chain(*sc.client_cert)

    return ctx
