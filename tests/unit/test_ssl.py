from __future__ import annotations

import ssl
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from rabbitmq_management_sdk import Config, RabbitMQClient, RabbitMQError, RabbitMQVersion, SSLConfig, TransportError
from rabbitmq_management_sdk.client import utils

if TYPE_CHECKING:
    from pathlib import Path


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


@pytest.mark.unit
@pytest.mark.parametrize("certificate_source", ["ca_bundle", "client_cert"])
@pytest.mark.parametrize(
    ("remove_certificate", "cause_type"),
    [(False, ssl.SSLError), (True, FileNotFoundError)],
    ids=["invalid-pem", "removed-after-validation"],
)
def test_client_wraps_certificate_loading_failures(
    tmp_path: Path, certificate_source: str, remove_certificate: bool, cause_type: type[OSError]
) -> None:
    certificate = tmp_path / "certificate.pem"
    certificate.write_text("not a PEM certificate", encoding="utf-8")
    if certificate_source == "ca_bundle":
        config = Config(
            host="localhost",
            username="fixture-user",
            password="fixture-password",
            ssl_context=SSLConfig(ca_bundle=certificate),
            version_override=RabbitMQVersion.parse("4.3.0"),
        )
    else:
        key = tmp_path / "client.key"
        key.write_text("not a PEM key", encoding="utf-8")
        config = Config(
            host="localhost",
            username=None,
            password=None,
            ssl_context=SSLConfig(client_cert=(certificate, key)),
            version_override=RabbitMQVersion.parse("4.3.0"),
        )
    if remove_certificate:
        certificate.unlink()

    with (
        patch("rabbitmq_management_sdk.client.rabbitmq_client.factory.create_adapter") as create_adapter,
        pytest.raises(TransportError, match="TLS") as error,
    ):
        RabbitMQClient(config)

    assert isinstance(error.value, RabbitMQError)
    assert isinstance(error.value.__cause__, cause_type)
    create_adapter.assert_not_called()
