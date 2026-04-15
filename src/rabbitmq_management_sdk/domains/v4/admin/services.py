from http import HTTPMethod
from typing import TYPE_CHECKING

from domains.v4.admin.schemas.vhost_response import VhostLimitResponse, VhostResponse

if TYPE_CHECKING:
    from domains.v4.admin.schemas.enums import VhostLimitName
    from domains.v4.admin.schemas.vhost_request import VhostLimitRequest, VhostRequest
    from http_adapter import HttpAdapter


class AdminManagerV4:
    def __init__(self, http_client: HttpAdapter, strict: bool) -> None:
        self._ha = http_client
        self._strict = strict

    def get_vhost(self, name: str) -> VhostResponse:
        data = (self._ha.request(method=HTTPMethod.GET, path=f"/api/vhosts/{name}")).json()
        return VhostResponse(**data)

    def get_all_vhosts(self) -> list[VhostResponse]:
        data = (self._ha.request(method=HTTPMethod.GET, path="/api/vhosts")).json()
        return [VhostResponse.model_validate(vr) for vr in data]

    def create_vhost(self, name: str, request: VhostRequest) -> None:
        self._ha.request(method=HTTPMethod.PUT, path=f"/api/vhosts/{name}", json=request.model_dump(exclude_none=True))

    def delete_vhost(self, name: str) -> None:
        self._ha.request(method=HTTPMethod.DELETE, path=f"/api/vhosts/{name}")

    def enable_vhost_deletion_protection(self, name: str) -> None:
        self._ha.request(method=HTTPMethod.POST, path=f"/api/vhosts/{name}/deletion/protection")

    def disable_vhost_deletion_protection(self, name: str) -> None:
        self._ha.request(method=HTTPMethod.DELETE, path=f"/api/vhosts/{name}/deletion/protection")

    def get_all_vhosts_limits(self) -> list[VhostLimitResponse]:
        data = (self._ha.request(method=HTTPMethod.GET, path="/api/vhost-limits")).json()
        return [VhostLimitResponse.model_validate(vhost) for vhost in data]

    def get_vhost_limits(self, vhost: str) -> VhostLimitResponse:
        data = (self._ha.request(method=HTTPMethod.GET, path=f"/api/vhost-limits/{vhost}")).json()
        return VhostLimitResponse.model_validate(data)

    def apply_vhost_limit(self, vhost: str, limit_name: VhostLimitName, request: VhostLimitRequest) -> None:
        self._ha.request(
            method=HTTPMethod.PUT,
            path=f"/api/vhost-limits/{vhost}/{limit_name}",
            json=request.model_dump(exclude_none=True),
        )

    def delete_vhost_limit(self, vhost: str, name: str) -> None:
        self._ha.request(method=HTTPMethod.DELETE, path=f"/api/vhost-limits/{vhost}/{name}")
