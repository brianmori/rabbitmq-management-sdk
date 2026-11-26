from __future__ import annotations

from http import HTTPMethod
from typing import TYPE_CHECKING

from rabbitmq_management_sdk._paths import quote_path_segment
from rabbitmq_management_sdk.resources.base import parse_list, parse_one
from rabbitmq_management_sdk.resources.v4._write_version import QUORUM_POLICY_WRITE_VERSIONS, validate_write_version
from rabbitmq_management_sdk.resources.v4.policies.schemas.policy_response import (
    OperatorPolicyResponse,
    PolicyResponse,
)

if TYPE_CHECKING:
    from rabbitmq_management_sdk.client.config import RabbitMQVersion
    from rabbitmq_management_sdk.http_adapter import HttpAdapter
    from rabbitmq_management_sdk.resources.v4.policies.schemas.policy_request import (
        OperatorPolicyRequest,
        PolicyRequest,
    )


def _policy_path(resource: str, vhost: str, name: str) -> str:
    return f"/api/{resource}/{vhost}/{quote_path_segment(name)}"


def _policy_payload(
    request: PolicyRequest | OperatorPolicyRequest, *, strict: bool, broker_version: RabbitMQVersion | None
) -> dict[str, object]:
    payload = request.model_dump(by_alias=True, exclude_none=True, exclude_defaults=not strict)
    validate_write_version(payload["definition"], broker_version, QUORUM_POLICY_WRITE_VERSIONS)
    return payload


class PolicyManager:
    """Manage regular RabbitMQ policies."""

    def __init__(
        self, http_client: HttpAdapter, vhost: str, strict: bool, *, broker_version: RabbitMQVersion | None = None
    ) -> None:
        self._ha = http_client
        self._vhost = vhost
        self._strict = strict
        self._broker_version = broker_version

    def get(self, name: str) -> PolicyResponse:
        """Return one policy by name in the configured virtual host."""
        return parse_one(
            self._ha.request(method=HTTPMethod.GET, path=_policy_path("policies", self._vhost, name)),
            PolicyResponse,
        )

    def list_by_vhost(self) -> list[PolicyResponse]:
        """Return all policies in the configured virtual host."""
        return parse_list(
            self._ha.request(method=HTTPMethod.GET, path=f"/api/policies/{self._vhost}"),
            PolicyResponse,
        )

    def list_all(self) -> list[PolicyResponse]:
        """Return all policies across every virtual host in the cluster."""
        return parse_list(
            self._ha.request(method=HTTPMethod.GET, path="/api/policies"),
            PolicyResponse,
        )

    def create(self, name: str, request: PolicyRequest) -> None:
        """Declare or update a policy in the configured virtual host."""
        self._ha.request(
            method=HTTPMethod.PUT,
            path=_policy_path("policies", self._vhost, name),
            json=_policy_payload(request, strict=self._strict, broker_version=self._broker_version),
        )

    def delete(self, name: str) -> None:
        """Delete a policy from the configured virtual host."""
        self._ha.request(method=HTTPMethod.DELETE, path=_policy_path("policies", self._vhost, name))


class OperatorPolicyManager:
    """Manage RabbitMQ operator policies."""

    def __init__(
        self, http_client: HttpAdapter, vhost: str, strict: bool, *, broker_version: RabbitMQVersion | None = None
    ) -> None:
        self._ha = http_client
        self._vhost = vhost
        self._strict = strict
        self._broker_version = broker_version

    def get(self, name: str) -> OperatorPolicyResponse:
        """Return one operator policy by name in the configured virtual host."""
        return parse_one(
            self._ha.request(
                method=HTTPMethod.GET,
                path=_policy_path("operator-policies", self._vhost, name),
            ),
            OperatorPolicyResponse,
        )

    def list_by_vhost(self) -> list[OperatorPolicyResponse]:
        """Return all operator policies in the configured virtual host."""
        return parse_list(
            self._ha.request(method=HTTPMethod.GET, path=f"/api/operator-policies/{self._vhost}"),
            OperatorPolicyResponse,
        )

    def list_all(self) -> list[OperatorPolicyResponse]:
        """Return all operator policies across every virtual host."""
        return parse_list(
            self._ha.request(method=HTTPMethod.GET, path="/api/operator-policies"),
            OperatorPolicyResponse,
        )

    def create(self, name: str, request: OperatorPolicyRequest) -> None:
        """Declare or update an operator policy in the configured virtual host."""
        self._ha.request(
            method=HTTPMethod.PUT,
            path=_policy_path("operator-policies", self._vhost, name),
            json=_policy_payload(request, strict=self._strict, broker_version=self._broker_version),
        )

    def delete(self, name: str) -> None:
        """Delete an operator policy from the configured virtual host."""
        self._ha.request(
            method=HTTPMethod.DELETE,
            path=_policy_path("operator-policies", self._vhost, name),
        )
