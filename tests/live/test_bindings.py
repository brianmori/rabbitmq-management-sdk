from __future__ import annotations

import contextlib
import json
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from rabbitmq_management_sdk import ClusterAuditor
from rabbitmq_management_sdk.resources.v4.bindings.schemas.binding_request import BindingRequest
from rabbitmq_management_sdk.resources.v4.exchanges.schemas.exchange_request import ExchangeRequest
from rabbitmq_management_sdk.resources.v4.queues.schemas.queue_request import ClassicQueueRequest, QueueRequest
from rabbitmq_management_sdk.topology import EdgeKind

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from rabbitmq_management_sdk.client.rabbitmq_client import RabbitMQClient


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
    # for name in created_names:
    #     rabbitmq_client_compatibility.exchanges.delete(name)


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
            rabbitmq_client_compatibility.queues.delete(name)


@pytest.mark.live
@pytest.mark.parametrize("exchange_type", ["direct", "topic"])
def test_create_destroy_exchange(
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
def test_export_and_audit_extended_headers_bindings(rabbitmq_client_compatibility: RabbitMQClient) -> None:
    client = rabbitmq_client_compatibility
    suffix = uuid4().hex
    exchange_name = f"sdk-headers-{suffix}"
    queue_name = f"sdk-headers-queue-{suffix}"
    client.exchanges.create(exchange_name, ExchangeRequest(type="headers"))
    try:
        client.queues.create(queue_name, QueueRequest(arguments=ClassicQueueRequest()))
        try:
            for match_mode in ("all-with-x", "any-with-x"):
                client.bindings.create_exchange_to_queue(
                    exchange_name,
                    queue_name,
                    BindingRequest(arguments={"x-match": match_mode, "x-region": "eu"}),
                )

            definitions = client.admin.export_definitions()
            exported_bindings = [
                binding
                for binding in definitions.bindings
                if binding.source == exchange_name and binding.destination == queue_name
            ]
            assert len(exported_bindings) == 2
            assert {binding.arguments.x_match for binding in exported_bindings} == {"all-with-x", "any-with-x"}

            auditor = ClusterAuditor(
                definitions,
                queues=client.queues.list_all(disable_stats=True),
                exchanges=client.exchanges.list_all(disable_stats=True),
            )
            edges = [
                edge
                for edge in auditor.topology.edges
                if edge.source.name == exchange_name and edge.target.name == queue_name
            ]
            assert len(edges) == 2
            assert {json.loads(edge.arguments or "{}")["x-match"] for edge in edges} == {"all-with-x", "any-with-x"}
            assert all(edge.kind == EdgeKind.BINDING and edge.routing_key == "" for edge in edges)
        finally:
            client.queues.delete(queue_name)
    finally:
        client.exchanges.delete(exchange_name)
