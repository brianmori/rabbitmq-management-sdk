from __future__ import annotations

from http import HTTPMethod
from typing import TYPE_CHECKING
from urllib.parse import quote

from rabbitmq_management_sdk.resources.base import parse_list, parse_one
from rabbitmq_management_sdk.resources.v4.policies.schemas.policy_response import (
    OperatorPolicyResponse,
    PolicyResponse,
)

if TYPE_CHECKING:
    from rabbitmq_management_sdk.http_adapter import HttpAdapter
    from rabbitmq_management_sdk.resources.v4.policies.schemas.policy_request import (
        OperatorPolicyRequest,
        PolicyRequest,
    )


def _policy_path(resource: str, vhost: str, name: str) -> str:
    return f"/api/{resource}/{vhost}/{quote(name, safe='')}"


class PolicyManager:
    """Manage regular RabbitMQ policies."""

    def __init__(self, http_client: HttpAdapter, vhost: str, strict: bool) -> None:
        self._ha = http_client
        self._vhost = vhost
        self._strict = strict

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
            json=request.model_dump(
                by_alias=True,
                exclude_none=True,
                exclude_defaults=not self._strict,
            ),
        )

    def delete(self, name: str) -> None:
        """Delete a policy from the configured virtual host."""
        self._ha.request(method=HTTPMethod.DELETE, path=_policy_path("policies", self._vhost, name))


class OperatorPolicyManager:
    """Manage RabbitMQ operator policies."""

    def __init__(self, http_client: HttpAdapter, vhost: str, strict: bool) -> None:
        self._ha = http_client
        self._vhost = vhost
        self._strict = strict

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
            json=request.model_dump(
                by_alias=True,
                exclude_none=True,
                exclude_defaults=not self._strict,
            ),
        )

    def delete(self, name: str) -> None:
        """Delete an operator policy from the configured virtual host."""
        self._ha.request(
            method=HTTPMethod.DELETE,
            path=_policy_path("operator-policies", self._vhost, name),
        )
