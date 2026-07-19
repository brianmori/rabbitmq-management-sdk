from __future__ import annotations

from http import HTTPMethod
from typing import TYPE_CHECKING

from rabbitmq_management_sdk.resources.base import parse_list, parse_one
from rabbitmq_management_sdk.resources.v4.admin.schemas.export_response import (
    ClusterDefinitionsResponse,
    VhostDefinitionsResponse,
)
from rabbitmq_management_sdk.resources.v4.admin.schemas.vhost_response import VhostLimitResponse, VhostResponse

if TYPE_CHECKING:
    from rabbitmq_management_sdk.http_adapter import HttpAdapter
    from rabbitmq_management_sdk.resources.v4.admin.schemas.vhost_request import (
        VhostLimitName,
        VhostLimitRequest,
        VhostRequest,
    )


class AdminManagerV4:
    def __init__(self, http_client: HttpAdapter, strict: bool) -> None:
        self._ha = http_client
        self._strict = strict

    def get_vhost(self, name: str) -> VhostResponse:
        return parse_one(self._ha.request(method=HTTPMethod.GET, path=f"/api/vhosts/{name}"), VhostResponse)

    def get_all_vhosts(self) -> list[VhostResponse]:
        return parse_list(self._ha.request(method=HTTPMethod.GET, path="/api/vhosts"), VhostResponse)

    def create_vhost(self, name: str, request: VhostRequest) -> None:
        self._ha.request(method=HTTPMethod.PUT, path=f"/api/vhosts/{name}", json=request.model_dump(exclude_none=True))

    def delete_vhost(self, name: str) -> None:
        self._ha.request(method=HTTPMethod.DELETE, path=f"/api/vhosts/{name}")

    def enable_vhost_deletion_protection(self, name: str) -> None:
        self._ha.request(method=HTTPMethod.POST, path=f"/api/vhosts/{name}/deletion/protection")

    def disable_vhost_deletion_protection(self, name: str) -> None:
        self._ha.request(method=HTTPMethod.DELETE, path=f"/api/vhosts/{name}/deletion/protection")

    def get_all_vhosts_limits(self) -> list[VhostLimitResponse]:
        return parse_list(self._ha.request(method=HTTPMethod.GET, path="/api/vhost-limits"), VhostLimitResponse)

    def get_vhost_limits(self, vhost: str) -> VhostLimitResponse | None:
        """Return the configured limits for a single vhost.

        The ``GET /api/vhost-limits/{vhost}`` endpoint returns a list that is
        empty when the vhost has no limits configured.

        Returns:
            The vhost's limits, or ``None`` when no limits are set.
        """
        limits = parse_list(
            self._ha.request(method=HTTPMethod.GET, path=f"/api/vhost-limits/{vhost}"), VhostLimitResponse
        )
        return limits[0] if limits else None

    def apply_vhost_limit(self, vhost: str, limit_name: VhostLimitName, request: VhostLimitRequest) -> None:
        self._ha.request(
            method=HTTPMethod.PUT,
            path=f"/api/vhost-limits/{vhost}/{limit_name}",
            json=request.model_dump(exclude_none=True, by_alias=True),
        )

    def delete_vhost_limit(self, vhost: str, limit_name: VhostLimitName) -> None:
        self._ha.request(method=HTTPMethod.DELETE, path=f"/api/vhost-limits/{vhost}/{limit_name}")

    def export_definitions(self) -> ClusterDefinitionsResponse:
        """GET /api/definitions — exports all cluster-wide definitions."""
        return parse_one(
            self._ha.request(method=HTTPMethod.GET, path="/api/definitions"),
            ClusterDefinitionsResponse,
        )

    def export_vhost_definitions(self, vhost: str) -> VhostDefinitionsResponse:
        """GET /api/definitions/{vhost} — exports definitions scoped to one vhost."""
        return parse_one(
            self._ha.request(method=HTTPMethod.GET, path=f"/api/definitions/{vhost}"),
            VhostDefinitionsResponse,
        )
