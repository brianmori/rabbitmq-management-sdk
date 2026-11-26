from __future__ import annotations

from http import HTTPMethod
from typing import TYPE_CHECKING

from rabbitmq_management_sdk._paths import quote_path_segment
from rabbitmq_management_sdk.resources.base import parse_list, parse_one
from rabbitmq_management_sdk.resources.v4._write_version import SHOVEL_WRITE_VERSIONS, validate_write_version
from rabbitmq_management_sdk.resources.v4.shovels.schemas.shovel_response import (
    ShovelParameterResponse,
    ShovelStatusResponse,
)

if TYPE_CHECKING:
    from rabbitmq_management_sdk.client.config import RabbitMQVersion
    from rabbitmq_management_sdk.http_adapter import HttpAdapter
    from rabbitmq_management_sdk.resources.v4.shovels.schemas.shovel_request import (
        ShovelRequest,
    )


class ShovelManager:
    def __init__(
        self, http_client: HttpAdapter, vhost: str, strict: bool, *, broker_version: RabbitMQVersion | None = None
    ) -> None:
        self._ha = http_client
        self._vhost = vhost
        self._strict = strict
        self._broker_version = broker_version

    def get(self, name: str) -> ShovelParameterResponse:
        return parse_one(
            self._ha.request(
                method=HTTPMethod.GET, path=f"/api/parameters/shovel/{self._vhost}/{quote_path_segment(name)}"
            ),
            ShovelParameterResponse,
        )

    def create(self, name: str, request: ShovelRequest) -> None:
        value = request.to_api_value()
        validate_write_version(value, self._broker_version, SHOVEL_WRITE_VERSIONS)
        self._ha.request(
            method=HTTPMethod.PUT,
            path=f"/api/parameters/shovel/{self._vhost}/{quote_path_segment(name)}",
            json={"value": value},
        )

    def delete(self, name: str) -> None:
        self._ha.request(
            method=HTTPMethod.DELETE, path=f"/api/parameters/shovel/{self._vhost}/{quote_path_segment(name)}"
        )

    def get_all_shovel_statuses(self) -> list[ShovelStatusResponse]:
        return parse_list(self._ha.request(method=HTTPMethod.GET, path="/api/shovels"), ShovelStatusResponse)

    def get_shovel_statuses_by_vhost(self) -> list[ShovelStatusResponse]:
        resp = self._ha.request(method=HTTPMethod.GET, path=f"/api/shovels/{self._vhost}")
        return parse_list(
            resp,
            ShovelStatusResponse,
        )

    def get_shovel_status(self, name: str) -> ShovelStatusResponse:
        resp = self._ha.request(
            method=HTTPMethod.GET, path=f"/api/shovels/vhost/{self._vhost}/{quote_path_segment(name)}"
        )
        return parse_one(
            resp,
            ShovelStatusResponse,
        )
