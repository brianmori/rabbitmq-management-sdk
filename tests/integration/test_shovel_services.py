"""Shovel response parsing against sanitized RabbitMQ captures."""

from pathlib import Path

import httpx
import pytest

from rabbitmq_management_sdk import ShovelParameterResponse
from rabbitmq_management_sdk.http_adapter.httpx import HttpxAdapter
from rabbitmq_management_sdk.resources.v4.shovels.services import ShovelManager

pytestmark = pytest.mark.integration

_FIXTURE_DIRECTORY = Path(__file__).parent / "rmq-4.2"


@pytest.mark.parametrize("capture", ["shovel-get.json", "shovel-get-detail-amqp10.json"])
@pytest.mark.parametrize("by_alias", [False, True], ids=["python-names", "wire-aliases"])
def test_captured_shovel_response_round_trip(capture: str, by_alias: bool) -> None:
    body = (_FIXTURE_DIRECTORY / capture).read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/parameters/shovel/src/captured-shovel"
        return httpx.Response(200, content=body)

    with HttpxAdapter(host="localhost", port=15672, transport=httpx.MockTransport(handler)) as adapter:
        response = ShovelManager(adapter, "src", False).get("captured-shovel")

    assert ShovelParameterResponse.model_validate_json(response.model_dump_json(by_alias=by_alias)) == response
