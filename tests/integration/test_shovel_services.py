"""Shovel response parsing against sanitized RabbitMQ captures."""

import json
from pathlib import Path

import httpx2
import pytest

from rabbitmq_management_sdk import (
    Amqp10ShovelDestination,
    Amqp10ShovelSource,
    ShovelParameterResponse,
    ShovelRequest,
)
from rabbitmq_management_sdk.http_adapter.httpx2 import Httpx2Adapter
from rabbitmq_management_sdk.resources.v4.shovels.services import ShovelManager

pytestmark = pytest.mark.integration

_FIXTURE_DIRECTORY = Path(__file__).parent / "rmq-4.2"


@pytest.mark.parametrize("strict", [False, True], ids=["compatibility", "strict"])
@pytest.mark.parametrize("consumer_name", [None, "archive-reader"], ids=["unnamed", "named"])
def test_amqp10_consumer_name_in_manager_request_and_response(strict: bool, consumer_name: str | None) -> None:
    expected_value: dict[str, object] = {
        "src-uri": "amqp://localhost",
        "dest-uri": "amqp://backup-host",
        "src-protocol": "amqp10",
        "src-address": "/queues/source",
        "src-prefetch-count": 1000,
        "dest-protocol": "amqp10",
        "dest-address": "/queues/archive",
        "dest-add-forward-headers": False,
        "dest-add-timestamp-header": False,
        "ack-mode": "on-confirm",
        "reconnect-delay": 1,
        "src-delete-after": "never",
    }
    if consumer_name is not None:
        expected_value["src-consumer-name"] = consumer_name
    methods: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.raw_path == b"/api/parameters/shovel/%2F/archive"
        methods.append(request.method)
        if request.method == "PUT":
            assert json.loads(request.content) == {"value": expected_value}
            return httpx2.Response(201)
        assert request.method == "GET"
        return httpx2.Response(
            200, json={"component": "shovel", "vhost": "/", "name": "archive", "value": expected_value}
        )

    with Httpx2Adapter(host="localhost", port=15672, transport=httpx2.MockTransport(handler)) as adapter:
        manager = ShovelManager(adapter, "%2F", strict)
        manager.create(
            "archive",
            ShovelRequest(
                src_uri="amqp://localhost",
                dest_uri="amqp://backup-host",
                src_arguments=Amqp10ShovelSource(src_address="/queues/source", src_consumer_name=consumer_name),
                dest_arguments=Amqp10ShovelDestination(dest_address="/queues/archive"),
            ),
        )
        response = manager.get("archive")

    assert methods == ["PUT", "GET"]
    assert response.value.src_arguments.src_protocol == "amqp10"
    assert response.value.src_arguments.src_consumer_name == consumer_name


@pytest.mark.parametrize("capture", ["shovel-get.json", "shovel-get-detail-amqp10.json"])
@pytest.mark.parametrize("by_alias", [False, True], ids=["python-names", "wire-aliases"])
def test_captured_shovel_response_round_trip(capture: str, by_alias: bool) -> None:
    body = (_FIXTURE_DIRECTORY / capture).read_bytes()

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/parameters/shovel/src/captured-shovel"
        return httpx2.Response(200, content=body)

    with Httpx2Adapter(host="localhost", port=15672, transport=httpx2.MockTransport(handler)) as adapter:
        response = ShovelManager(adapter, "src", False).get("captured-shovel")

    assert (
        ShovelParameterResponse.model_validate_json(response.model_dump_json(by_alias=by_alias), by_name=not by_alias)
        == response
    )
