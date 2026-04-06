import os
from dataclasses import dataclass

import pytest
from dotenv import find_dotenv, load_dotenv

from rabbitmq_management_sdk.rabbitmq_client.config import Config
from rabbitmq_management_sdk.rabbitmq_client.rabbitmq_client import RabbitMQClient

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
def rabbitmq_client(rabbit_config: RabbitSettings) -> RabbitMQClient:
    """Universal fixture for a RabbitMQ Manager."""
    # os.getenv returns None if the variable isn't set,
    # or you can provide a default like 'guest' for local dev.

    config = Config(host=rabbit_config.host, username=rabbit_config.username, password=rabbit_config.password)

    return RabbitMQClient(config)
