from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from rabbitmq_management_sdk.client.config import Config
from rabbitmq_management_sdk.client.rabbitmq_client import RabbitMQClient
from tests.shared.constants import VhostTest

if TYPE_CHECKING:
    from collections.abc import Iterator


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
def rabbitmq_client_compatibility(rabbit_config: RabbitSettings) -> Iterator[RabbitMQClient]:
    """Universal fixture for a RabbitMQ Manager in Compatibility Mode."""
    config = Config(
        host=rabbit_config.host,
        port=rabbit_config.port,
        username=rabbit_config.username,
        password=rabbit_config.password,
        strict=False,
    )
    with RabbitMQClient(config) as client:
        yield client


@pytest.fixture
def rabbitmq_client_strict(rabbit_config: RabbitSettings) -> Iterator[RabbitMQClient]:
    """Universal fixture for a RabbitMQ Manager in Strict Mode."""
    config = Config(
        host=rabbit_config.host,
        port=rabbit_config.port,
        username=rabbit_config.username,
        password=rabbit_config.password,
        strict=True,
    )
    with RabbitMQClient(config) as client:
        yield client


@pytest.fixture
def rabbitmq_client_strict_vhost_src(rabbit_config: RabbitSettings) -> Iterator[RabbitMQClient]:
    """Universal fixture for a RabbitMQ Manager in Strict Mode."""
    config = Config(
        host=rabbit_config.host,
        port=rabbit_config.port,
        username=rabbit_config.username,
        password=rabbit_config.password,
        strict=True,
        virtual_host=VhostTest.SRC,
    )
    with RabbitMQClient(config) as client:
        yield client


@pytest.fixture
def rabbitmq_client_strict_vhost_dest(rabbit_config: RabbitSettings) -> Iterator[RabbitMQClient]:
    """Universal fixture for a RabbitMQ Manager in Strict Mode."""
    config = Config(
        host=rabbit_config.host,
        port=rabbit_config.port,
        username=rabbit_config.username,
        password=rabbit_config.password,
        strict=True,
        virtual_host=VhostTest.DST,
    )
    with RabbitMQClient(config) as client:
        yield client
