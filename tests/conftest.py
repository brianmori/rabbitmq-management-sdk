from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from rabbitmq_management_sdk.client.config import Config
from rabbitmq_management_sdk.client.rabbitmq_client import RabbitMQClient

if TYPE_CHECKING:
    from collections.abc import Iterator


@dataclass(frozen=True)
class RabbitSettings:
    host: str
    username: str
    password: str = field(repr=False)
    port: int

    def client_config(self, *, virtual_host: str = "/", strict: bool = False) -> Config:
        return Config(
            host=self.host,
            port=self.port,
            username=self.username,
            password=self.password,
            virtual_host=virtual_host,
            strict=strict,
        )


@pytest.fixture(scope="session")
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
    with RabbitMQClient(rabbit_config.client_config()) as client:
        yield client
