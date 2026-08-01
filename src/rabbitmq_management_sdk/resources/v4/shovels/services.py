from __future__ import annotations

from http import HTTPMethod
from typing import TYPE_CHECKING

from rabbitmq_management_sdk.resources.base import parse_list, parse_one
from rabbitmq_management_sdk.resources.v4.shovels.schemas.shovel_response import (
    ShovelParameterResponse,
    ShovelStatusResponse,
)

if TYPE_CHECKING:
    from rabbitmq_management_sdk.http_adapter import HttpAdapter
    from rabbitmq_management_sdk.resources.v4.shovels.schemas.shovel_request import (
        ShovelRequest,
    )


class ShovelManagerV4:
    def __init__(self, http_client: HttpAdapter, vhost: str, strict: bool) -> None:
        self._ha = http_client
        self._vhost = vhost
        self._strict = strict

    def get(self, name: str) -> ShovelParameterResponse:
        return parse_one(
            self._ha.request(method=HTTPMethod.GET, path=f"/api/parameters/shovel/{self._vhost}/{name}"),
            ShovelParameterResponse,
        )

    def create(self, name: str, request: ShovelRequest) -> None:
        self._ha.request(
            method=HTTPMethod.PUT,
            path=f"/api/parameters/shovel/{self._vhost}/{name}",
            json={"value": request.to_api_value()},
        )

    def delete(self, name: str) -> None:
        self._ha.request(method=HTTPMethod.DELETE, path=f"/api/parameters/shovel/{self._vhost}/{name}")

    def get_all_shovel_statuses(self) -> list[ShovelStatusResponse]:
        return parse_list(self._ha.request(method=HTTPMethod.GET, path="/api/shovels"), ShovelStatusResponse)

    def get_shovel_statuses_by_vhost(self) -> list[ShovelStatusResponse]:
        resp = self._ha.request(method=HTTPMethod.GET, path=f"/api/shovels/{self._vhost}")
        return parse_list(
            resp,
            ShovelStatusResponse,
        )

    def get_shovel_status(self, name: str) -> ShovelStatusResponse:
        resp = self._ha.request(method=HTTPMethod.GET, path=f"/api/shovels/vhost/{self._vhost}/{name}")
        return parse_one(
            resp,
            ShovelStatusResponse,
        )
