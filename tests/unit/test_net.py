import ssl

import pytest
from client import utils
from client.config import SSLConfig


@pytest.mark.unit
def test_tls_13() -> None:
    """Placeholder test to ensure test discovery works."""
    sc_tls13 = SSLConfig(min_version=ssl.TLSVersion.TLSv1_3)

    ssl_context = utils.create_ssl_context(sc_tls13)

    assert ssl_context.minimum_version == ssl.TLSVersion.TLSv1_3


@pytest.mark.unit
def test_tls_default() -> None:
    """Placeholder test to ensure test discovery works."""
    sc_tls = SSLConfig()

    ssl_context = utils.create_ssl_context(sc_tls)

    assert ssl_context.minimum_version == ssl.TLSVersion.TLSv1_2
