from __future__ import annotations

import re
from http import HTTPMethod
from typing import TYPE_CHECKING

from pydantic import ValidationError

from rabbitmq_management_sdk._paths import quote_path_segment
from rabbitmq_management_sdk.exceptions import NotFoundError, RabbitMQError
from rabbitmq_management_sdk.resources.base import parse_list, parse_one
from rabbitmq_management_sdk.resources.v4._write_version import QUORUM_POLICY_WRITE_VERSIONS, validate_write_version
from rabbitmq_management_sdk.resources.v4.policies.schemas.common import PolicyApplyTo
from rabbitmq_management_sdk.resources.v4.policies.schemas.policy_request import PolicyDefinition
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
        self._put(name, _policy_payload(request, strict=self._strict, broker_version=self._broker_version))

    def set_stream_retention(
        self, name: str, *, stream_name: str, max_age: str, max_length_bytes: int, priority: int
    ) -> None:
        r"""Set age and byte retention in a dedicated regular stream policy.

        The policy matches one literal name from ``\A`` through ``\z``. An existing
        policy must have that pattern, stream scope, and the supplied priority.
        Its other definition keys are carried forward. After the write, read the
        stream to verify the selected policy and effective definition.

        Args:
            name: The raw name of the dedicated policy.
            stream_name: The raw name of an existing stream in this virtual host.
            max_age: Positive duration with a unit such as ``7D`` or ``24h``.
            max_length_bytes: Retention size limit in bytes.
            priority: Explicit regular policy priority.

        Raises:
            RabbitMQError: If the retention input is invalid, the queue is of
                another type, the policy name is already used at a different
                scope or priority, or the stream reports another regular policy.
            NotFoundError: If the stream does not exist.

        RabbitMQ provides policy upserts. Coordinate concurrent policy writers
        for this stream and policy name during the read and write.
        """
        # Queue responses depend on the policy schemas, so resolve this dependency at call time.
        from rabbitmq_management_sdk.resources.v4.queues.services import QueueManager  # noqa: PLC0415

        try:
            changes = PolicyDefinition(max_age=max_age, max_length_bytes=max_length_bytes)
        except ValidationError as exc:
            raise RabbitMQError("Invalid stream retention settings") from exc

        queue = QueueManager(self._ha, self._vhost, self._strict).get(stream_name, disable_stats=True)
        if queue.type != "stream":
            raise RabbitMQError(f"Queue {stream_name!r} has type {queue.type!r}. Stream retention requires a stream")
        if queue.policy and queue.policy != name:
            raise RabbitMQError(f"Stream {stream_name!r} selects regular policy {queue.policy!r}. Review it first")

        pattern = rf"\A{re.escape(stream_name)}\z"
        definition = changes.model_dump(by_alias=True, exclude_none=True)
        try:
            existing = self.get(name)
        except NotFoundError:
            existing = None
        if existing is not None:
            if (existing.pattern, existing.apply_to, existing.priority) != (pattern, PolicyApplyTo.STREAMS, priority):
                raise RabbitMQError(f"Policy {name!r} already exists with a different pattern, scope, or priority")
            definition = existing.definition.model_dump() | definition

        validate_write_version(definition, self._broker_version, QUORUM_POLICY_WRITE_VERSIONS)
        self._put(
            name,
            {
                "pattern": pattern,
                "apply-to": PolicyApplyTo.STREAMS.value,
                "priority": priority,
                "definition": definition,
            },
        )

    def delete(self, name: str) -> None:
        """Delete a policy from the configured virtual host."""
        self._ha.request(method=HTTPMethod.DELETE, path=_policy_path("policies", self._vhost, name))

    def _put(self, name: str, payload: dict[str, object]) -> None:
        self._ha.request(method=HTTPMethod.PUT, path=_policy_path("policies", self._vhost, name), json=payload)


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
