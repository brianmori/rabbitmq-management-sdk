import ssl
from typing import TYPE_CHECKING
from urllib.parse import quote

if TYPE_CHECKING:
    from rabbitmq_management_sdk.client.config import SSLConfig


def encode_vhost(vhost: str) -> str:
    """Encodes a virtual host name for the RabbitMQ Management API.

    Characters like '/' are converted to '%2F' and whitespaces are
    properly escaped to ensure URL compatibility.

    Args:
        vhost: The virtual host to encode.

    Returns:
        The URL-safe encoded virtual host string.
    """
    return quote(vhost, safe="")


def create_ssl_context(sc: SSLConfig) -> ssl.SSLContext:
    """Creates a SSL context for the RabbitMQ Management API.

    TLS Version defaults to TLSv1.2 and hostname verification is enabled.

    Args:
        sc: SSL configuration for the client.

    Returns:
        An SSL context configured with the provided SSL settings.
    """
    ctx = ssl.create_default_context(cafile=sc.ca_bundle)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = True

    if not sc.verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    if sc.client_cert:
        ctx.load_cert_chain(*sc.client_cert)

    return ctx
