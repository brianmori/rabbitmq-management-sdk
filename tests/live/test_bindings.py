from __future__ import annotations

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
    from rabbitmq_management_sdk.client.rabbitmq_client import RabbitMQClient


@pytest.mark.live
def test_export_and_audit_extended_headers_bindings(live_client: RabbitMQClient) -> None:
    client = live_client
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
