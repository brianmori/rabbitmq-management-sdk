import contextlib
from typing import TYPE_CHECKING

import pytest

from rabbitmq_management_sdk.domains.v4.bindings.schemas.binding_request import BindingRequest
from rabbitmq_management_sdk.domains.v4.exchanges.schemas.exchange_request import ExchangeRequest
from rabbitmq_management_sdk.domains.v4.queues.schemas.queue_request import ClassicQueueRequest, QueueRequest

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from rabbitmq_management_sdk.client.rabbitmq_client import RabbitMQClient


@pytest.fixture(params=["direct", "topic", "fanout"])
def exchange_type(request: str) -> str:
    return request


@pytest.fixture
def exchange_factory(rabbitmq_client_compatibility: RabbitMQClient) -> Iterator[Callable[[str, str], str]]:

    created_names = []

    # This is the function the test actually receives
    def _make_exchange(name_ex: str, ex_type: str = "direct") -> str:
        rabbitmq_client_compatibility.exchanges.create(name_ex, ExchangeRequest(type=ex_type))
        created_names.append(name_ex)  # Track it for cleanup
        return name_ex

    yield _make_exchange  # Send the function to the test

    # Teardown: Loop through everything created in the test
    for name in created_names:
        rabbitmq_client_compatibility.exchanges.delete(name)
        pass


@pytest.fixture
def temp_queue(rabbitmq_client_compatibility: RabbitMQClient, exchange_type: str) -> Iterator[str]:
    name = f"test-{exchange_type}-exch"
    # Create the specific type based on the parameter

    rabbitmq_client_compatibility.queues.create(
        name, QueueRequest(durable=True, auto_delete=False, arguments=ClassicQueueRequest())
    )
    try:
        yield name
    finally:
        with contextlib.suppress(Exception):
            pass
            rabbitmq_client_compatibility.queues.delete(name)


@pytest.mark.live
@pytest.mark.parametrize("exchange_type", ["direct", "topic"])
def test_create_destroy_direct_exchange(
    rabbitmq_client_compatibility: RabbitMQClient, exchange_factory: Callable[[str, str], str], temp_queue: str
) -> None:
    src = exchange_factory("source_ex", "topic")
    dest = exchange_factory("dest_ex", "direct")

    rabbitmq_client_compatibility.bindings.create_exchange_to_exchange(
        source=src, destination=dest, request=BindingRequest(routing_key="test.#")
    )

    rabbitmq_client_compatibility.bindings.create_exchange_to_queue(
        src, temp_queue, request=BindingRequest(routing_key="test.#")
    )


@pytest.mark.live
def test_fix(rabbitmq_client_compatibility: RabbitMQClient, exchange_type: str) -> None:
    print(exchange_type)
    pass
