"""Literal argument keys survive HTTP, capture, and in-memory boundaries."""

import json
from pathlib import Path
from typing import Any

import httpx2
import pytest
from pydantic import ValidationError
from tests.shared.parser_fixtures import _exchange, _queue

from rabbitmq_management_sdk import ClusterAuditor
from rabbitmq_management_sdk.exceptions import (
    MalformedResponseError,
    TopologyDefinitionsError,
    TopologyResourceSnapshotError,
)
from rabbitmq_management_sdk.http_adapter.httpx2 import Httpx2Adapter
from rabbitmq_management_sdk.resources.v4.admin.schemas.export_response import ClusterDefinitionsResponse
from rabbitmq_management_sdk.resources.v4.admin.services import AdminManager
from rabbitmq_management_sdk.resources.v4.exchanges.schemas.exchange_response import ExchangeResponse
from rabbitmq_management_sdk.resources.v4.exchanges.services import ExchangeManager
from rabbitmq_management_sdk.resources.v4.queues.schemas.queue_response import QueueResponse
from rabbitmq_management_sdk.topology import EdgeKind

pytestmark = pytest.mark.integration


@pytest.fixture
def definitions_payload() -> dict[str, Any]:
    """A synthetic capture with both reserved and ordinary argument keys."""
    return {
        "queues": [
            _queue("q", "/", **{"x-queue-type": "classic", "dead_letter_exchange": "ghost-dlx"}),
            _queue(
                "routed-q",
                "/",
                **{
                    "x-queue-type": "classic",
                    "x-dead-letter-exchange": "real-dlx",
                    "dead_letter_exchange": "ghost-dlx",
                },
            ),
        ],
        "exchanges": [
            _exchange("headers", "/", type_="headers", alternate_exchange="ghost-ae"),
            _exchange("routed-ex", "/", **{"alternate-exchange": "real-ae", "alternate_exchange": "ghost-ae"}),
        ],
        "bindings": [
            {
                "source": "headers",
                "vhost": "/",
                "destination": "q",
                "destination_type": "queue",
                "routing_key": "",
                "arguments": arguments,
            }
            for arguments in (
                {"x_match": "any"},
                {"x-match": "any"},
                {"x_match": "custom-value", "x-match": "all-with-x", "nested": {"null": None, "items": [1, None]}},
            )
        ],
    }


@pytest.fixture
def exchange_payload() -> dict[str, Any]:
    return _exchange("observed", "/", alternate_exchange={"ordinary": [None]})


@pytest.fixture
def queue_payloads(definitions_payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {**queue, "type": "classic", "exclusive": False, "node": "rabbit@synthetic", "state": "running"}
        for queue in definitions_payload["queues"]
    ]


@pytest.fixture
def exchange_payloads(definitions_payload: dict[str, Any], exchange_payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        *definitions_payload["exchanges"],
        exchange_payload,
        _exchange("observed-routed", "/", **{"alternate-exchange": "observed-ae", "alternate_exchange": "ghost-ae"}),
    ]


@pytest.mark.parametrize("boundary", ["memory", "http", "files"])
def test_literal_keys_retain_topology_through_capture_boundaries(
    boundary: str,
    definitions_payload: dict[str, Any],
    queue_payloads: list[dict[str, Any]],
    exchange_payloads: list[dict[str, Any]],
    tmp_path: Path,
) -> None:
    queues = [QueueResponse.model_validate(queue) for queue in queue_payloads]
    if boundary == "memory":
        definitions = ClusterDefinitionsResponse.model_validate(definitions_payload)
        exchanges = [ExchangeResponse.model_validate(exchange) for exchange in exchange_payloads]
        auditor = ClusterAuditor(definitions, queues=queues, exchanges=exchanges)
    elif boundary == "http":

        def handler(request: httpx2.Request) -> httpx2.Response:
            if request.url.path == "/api/definitions":
                return httpx2.Response(200, json=definitions_payload)
            name = request.url.path.rsplit("/", 1)[-1]
            payload = next(exchange for exchange in exchange_payloads if exchange["name"] == name)
            return httpx2.Response(200, json=payload)

        with Httpx2Adapter("localhost", port=15672, transport=httpx2.MockTransport(handler)) as adapter:
            definitions = AdminManager(adapter, False).export_definitions()
            manager = ExchangeManager(adapter, "%2F", False)
            exchanges = [manager.get(exchange["name"]) for exchange in exchange_payloads]
        auditor = ClusterAuditor(definitions, queues=queues, exchanges=exchanges)
    else:
        definitions_path = tmp_path / "definitions.json"
        queues_path = tmp_path / "queues.json"
        exchanges_path = tmp_path / "exchanges.json"
        definitions_path.write_text(json.dumps(definitions_payload), encoding="utf-8")
        queues_path.write_text(json.dumps(queue_payloads), encoding="utf-8")
        exchanges_path.write_text(json.dumps(exchange_payloads), encoding="utf-8")
        auditor = ClusterAuditor.from_files(definitions_path, queues_path=queues_path, exchanges_path=exchanges_path)

    bindings = [edge for edge in auditor.topology.edges if edge.kind == EdgeKind.BINDING]
    assert {edge.arguments for edge in bindings} == {
        json.dumps(binding["arguments"], sort_keys=True) for binding in definitions_payload["bindings"]
    }
    routes = {
        (edge.kind, edge.source.name, edge.target.name) for edge in auditor.topology.edges if edge not in bindings
    }
    assert routes == {
        (EdgeKind.DEAD_LETTER, "routed-q", "real-dlx"),
        (EdgeKind.ALTERNATE_EXCHANGE, "routed-ex", "real-ae"),
        (EdgeKind.ALTERNATE_EXCHANGE, "observed-routed", "observed-ae"),
    }
    assert auditor.definitions.model_dump(by_alias=True, exclude_unset=True) == definitions_payload


@pytest.mark.parametrize(
    "arguments",
    [
        {"alternate_exchange": {"ordinary": [None]}},
        {"alternate_exchange": "ordinary-value", "alternate-exchange": None},
        {"alternate_exchange": None, "alternate-exchange": "real-ae"},
    ],
)
def test_paginated_exchange_response_preserves_literal_argument_keys(
    arguments: dict[str, object], exchange_payload: dict[str, Any]
) -> None:
    exchange_payload["arguments"] = arguments

    def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "items": [exchange_payload],
                "page": 1,
                "page_count": 1,
                "page_size": 100,
                "total_count": 1,
                "filtered_count": 1,
                "item_count": 1,
            },
        )

    with Httpx2Adapter("localhost", port=15672, transport=httpx2.MockTransport(handler)) as adapter:
        (exchange,) = ExchangeManager(adapter, "%2F", False).list_all()

    assert exchange.arguments.alternate_exchange == arguments.get("alternate-exchange")
    assert exchange.arguments.model_dump(by_alias=True, exclude_unset=True) == arguments


@pytest.mark.parametrize("boundary", ["http", "files"])
@pytest.mark.parametrize("resource", ["binding", "exchange"])
def test_reserved_wire_key_validation_retains_public_exception_boundaries(
    boundary: str,
    resource: str,
    definitions_payload: dict[str, Any],
    exchange_payload: dict[str, Any],
    tmp_path: Path,
) -> None:
    definitions_payload["bindings"][0]["arguments"] = {"x-match": "invalid-mode"}
    exchange_payload["arguments"] = {"alternate-exchange": {"invalid": "object"}}
    if boundary == "http":

        def handler(_request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(200, json=definitions_payload if resource == "binding" else exchange_payload)

        with (
            Httpx2Adapter("localhost", port=15672, transport=httpx2.MockTransport(handler)) as adapter,
            pytest.raises(MalformedResponseError) as http_error,
        ):
            if resource == "binding":
                AdminManager(adapter, False).export_definitions()
            else:
                ExchangeManager(adapter, "%2F", False).get("observed")
        cause = http_error.value.__cause__
    else:
        definitions_path = tmp_path / "definitions.json"
        queues_path = tmp_path / "queues.json"
        exchanges_path = tmp_path / "exchanges.json"
        definitions_path.write_text(
            json.dumps(definitions_payload if resource == "binding" else {"queues": []}), encoding="utf-8"
        )
        queues_path.write_text("[]", encoding="utf-8")
        exchanges_path.write_text(json.dumps([exchange_payload]), encoding="utf-8")
        expected_error = TopologyDefinitionsError if resource == "binding" else TopologyResourceSnapshotError
        with pytest.raises(expected_error) as file_error:
            ClusterAuditor.from_files(definitions_path, queues_path=queues_path, exchanges_path=exchanges_path)
        cause = file_error.value.__cause__

    assert isinstance(cause, ValidationError)
