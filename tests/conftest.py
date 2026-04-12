import os
from dataclasses import dataclass

import pytest
from dotenv import find_dotenv, load_dotenv

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
