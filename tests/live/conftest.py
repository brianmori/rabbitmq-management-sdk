"""Explicit disposable resources; other tests need no broker."""

from __future__ import annotations

from contextlib import ExitStack, suppress
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from tests.shared.live_broker import ManagementAPI

from rabbitmq_management_sdk import NotFoundError, RabbitMQClient, VhostRequest
from rabbitmq_management_sdk.http_adapter import factory
from rabbitmq_management_sdk.http_adapter.config import BasicAuthentication, TimeoutConfig

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from tests.conftest import RabbitSettings


def _delete_vhost(client: RabbitMQClient, name: str) -> None:
    with suppress(NotFoundError):
        client.admin.delete_vhost(name)


@pytest.fixture
def vhost_factory(rabbitmq_client_compatibility: RabbitMQClient) -> Iterator[Callable[[VhostRequest | None], str]]:
    with ExitStack() as stack:

        def create(request: VhostRequest | None = None) -> str:
            name = f"sdk-test-{uuid4().hex}"
            stack.callback(_delete_vhost, rabbitmq_client_compatibility, name)
            rabbitmq_client_compatibility.admin.create_vhost(name, request or VhostRequest())
            return name

        yield create


@pytest.fixture
def live_vhost(vhost_factory: Callable[[VhostRequest | None], str]) -> str:
    return vhost_factory(None)


@pytest.fixture
def live_client(rabbit_config: RabbitSettings, live_vhost: str) -> Iterator[RabbitMQClient]:
    with RabbitMQClient(rabbit_config.client_config(virtual_host=live_vhost)) as client:
        yield client


@pytest.fixture(scope="module")
def management_api(rabbit_config: RabbitSettings) -> Iterator[ManagementAPI]:
    auth = BasicAuthentication(rabbit_config.username, rabbit_config.password)
    with factory.create_adapter(
        rabbit_config.host,
        port=rabbit_config.port,
        default_headers={"Authorization": auth.auth_header},
        timeout=TimeoutConfig(read=60),
        max_retries=0,
    ) as adapter:
        yield ManagementAPI(adapter)
