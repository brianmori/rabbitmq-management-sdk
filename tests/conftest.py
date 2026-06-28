from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum

import pytest
from dotenv import find_dotenv, load_dotenv

from rabbitmq_management_sdk import VhostRequest
from rabbitmq_management_sdk.client.config import Config
from rabbitmq_management_sdk.client.rabbitmq_client import RabbitMQClient

load_dotenv(find_dotenv())


@dataclass
class RabbitSettings:
    host: str
    username: str
    password: str
    port: int


@pytest.fixture
def rabbit_config() -> RabbitSettings:
    return RabbitSettings(
        host=os.getenv("RABBIT_HOST", "localhost"),
        port=int(os.getenv("RABBIT_PORT", "15672")),
        username=os.getenv("RABBIT_USER", "guest"),
        password=os.getenv("RABBIT_PASS", "guest"),
    )


@pytest.fixture
def rabbitmq_client_compatibility(rabbit_config: RabbitSettings) -> RabbitMQClient:
    """Universal fixture for a RabbitMQ Manager in Compatibility Mode."""

    config = Config(
        host=rabbit_config.host,
        port=rabbit_config.port,
        username=rabbit_config.username,
        password=rabbit_config.password,
        strict=False,
    )
    return RabbitMQClient(config)


@pytest.fixture
def rabbitmq_client_strict(rabbit_config: RabbitSettings) -> RabbitMQClient:
    """Universal fixture for a RabbitMQ Manager in Strict Mode."""

    config = Config(
        host=rabbit_config.host,
        port=rabbit_config.port,
        username=rabbit_config.username,
        password=rabbit_config.password,
        strict=True,
    )
    return RabbitMQClient(config)


@pytest.fixture
def rabbitmq_client_strict_vhost_src(rabbit_config: RabbitSettings) -> RabbitMQClient:
    """Universal fixture for a RabbitMQ Manager in Strict Mode."""

    config = Config(
        host=rabbit_config.host,
        port=rabbit_config.port,
        username=rabbit_config.username,
        password=rabbit_config.password,
        strict=True,
        virtual_host=TestVhost.SRC,
    )
    return RabbitMQClient(config)


@pytest.fixture
def rabbitmq_client_strict_vhost_dest(rabbit_config: RabbitSettings) -> RabbitMQClient:
    """Universal fixture for a RabbitMQ Manager in Strict Mode."""

    config = Config(
        host=rabbit_config.host,
        port=rabbit_config.port,
        username=rabbit_config.username,
        password=rabbit_config.password,
        strict=True,
        virtual_host=TestVhost.DST,
    )
    return RabbitMQClient(config)


class TestVhost(StrEnum):
    SRC = "test-src"
    DST = "test-dst"


@pytest.fixture(autouse=True)
def test_create_test_vhost(rabbitmq_client_compatibility: RabbitMQClient) -> None:
    vhost_service = rabbitmq_client_compatibility.admin

    # Create a new vhost
    vhost_request = VhostRequest(description="Test Vhost", tags=["test"])
    vhost_service.create_vhost(TestVhost.SRC, vhost_request)
    vhost_service.create_vhost(TestVhost.DST, vhost_request)
