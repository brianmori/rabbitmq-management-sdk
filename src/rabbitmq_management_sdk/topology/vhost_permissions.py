"""Project captured vhost grants and resource counts without evaluating authorization."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from rabbitmq_management_sdk.exceptions import TopologyValidationError

if TYPE_CHECKING:
    from collections.abc import Iterable

    from rabbitmq_management_sdk.resources.v4.admin.schemas.export_response import ClusterDefinitionsResponse
    from rabbitmq_management_sdk.topology.models import ClusterTopology


@dataclass(frozen=True, slots=True, order=True)
class VhostPermission:
    """One captured principal's separate configure, write and read patterns."""

    principal: str
    configure_pattern: str
    write_pattern: str
    read_pattern: str

    def __post_init__(self) -> None:
        """Validate string fields without interpreting any pattern."""
        if not all(
            isinstance(value, str)
            for value in (self.principal, self.configure_pattern, self.write_pattern, self.read_pattern)
        ):
            raise TopologyValidationError("VhostPermission fields must be strings")


@dataclass(frozen=True, slots=True, order=True)
class VhostTopicPermission:
    """One captured principal's write and read patterns for a topic exchange."""

    principal: str
    exchange: str
    write_pattern: str
    read_pattern: str

    def __post_init__(self) -> None:
        """Validate string fields without interpreting any pattern."""
        if not all(
            isinstance(value, str) for value in (self.principal, self.exchange, self.write_pattern, self.read_pattern)
        ):
            raise TopologyValidationError("VhostTopicPermission fields must be strings")


class VhostLimitStatus(StrEnum):
    """Meaning of one captured queue or connection count limit."""

    UNAVAILABLE = "unavailable"
    UNSET = "unset"
    CLEARED = "cleared"
    UNLIMITED = "unlimited"
    BLOCKED = "blocked"
    CAPPED = "capped"


def _numeric_limit_status(value: int) -> VhostLimitStatus:
    if value < 0:
        return VhostLimitStatus.UNLIMITED
    if value == 0:
        return VhostLimitStatus.BLOCKED
    return VhostLimitStatus.CAPPED


@dataclass(frozen=True, slots=True)
class VhostLimit:
    """The broker meaning and original integer value of one vhost limit."""

    status: VhostLimitStatus = VhostLimitStatus.UNAVAILABLE
    value: int | None = None

    def __post_init__(self) -> None:
        """Check that the limit's retained value agrees with its status."""
        if not isinstance(self.status, VhostLimitStatus):
            raise TopologyValidationError("VhostLimit.status must be a VhostLimitStatus")
        if self.value is None:
            if self.status not in {VhostLimitStatus.UNAVAILABLE, VhostLimitStatus.UNSET, VhostLimitStatus.CLEARED}:
                raise TopologyValidationError("VhostLimit status requires an integer value")
        elif type(self.value) is not int or self.status != _numeric_limit_status(self.value):
            raise TopologyValidationError("VhostLimit status must agree with its integer value")


@dataclass(frozen=True, slots=True)
class VhostPermissions:
    """Captured ordinary permissions, topic permissions and limits for one vhost.

    A permissions collection is None when its section was omitted and
    an empty tuple when the section was supplied without entries for this vhost.
    """

    vhost: str
    permissions: tuple[VhostPermission, ...] | None = None
    topic_permissions: tuple[VhostTopicPermission, ...] | None = None
    max_queues: VhostLimit = VhostLimit()
    max_connections: VhostLimit = VhostLimit()

    def __post_init__(self) -> None:
        """Validate the vhost row and order declarations by their fields."""
        if not isinstance(self.vhost, str):
            raise TopologyValidationError("VhostPermissions.vhost must be a string")
        if self.permissions is not None:
            if not isinstance(self.permissions, tuple) or any(
                not isinstance(item, VhostPermission) for item in self.permissions
            ):
                raise TopologyValidationError("VhostPermissions.permissions must be a tuple of VhostPermission or None")
            object.__setattr__(self, "permissions", tuple(sorted(self.permissions)))
        if self.topic_permissions is not None:
            if not isinstance(self.topic_permissions, tuple) or any(
                not isinstance(item, VhostTopicPermission) for item in self.topic_permissions
            ):
                raise TopologyValidationError(
                    "VhostPermissions.topic_permissions must be a tuple of topic grants or None"
                )
            object.__setattr__(self, "topic_permissions", tuple(sorted(self.topic_permissions)))
        if not isinstance(self.max_queues, VhostLimit) or not isinstance(self.max_connections, VhostLimit):
            raise TopologyValidationError("VhostPermissions limits must be VhostLimit values")


@dataclass(frozen=True, slots=True)
class VhostPermissionsInventory:
    """Captured vhost facts in name order with independent section presence."""

    vhosts: tuple[VhostPermissions, ...] = ()
    permissions_captured: bool = False
    topic_permissions_captured: bool = False

    def __post_init__(self) -> None:
        """Validate section presence and normalize vhost order."""
        if not isinstance(self.permissions_captured, bool) or not isinstance(self.topic_permissions_captured, bool):
            raise TopologyValidationError("VhostPermissionsInventory section presence must be boolean")
        if not isinstance(self.vhosts, tuple) or any(not isinstance(vhost, VhostPermissions) for vhost in self.vhosts):
            raise TopologyValidationError("VhostPermissionsInventory.vhosts must be a tuple of VhostPermissions")
        if len({vhost.vhost for vhost in self.vhosts}) != len(self.vhosts):
            raise TopologyValidationError("VhostPermissionsInventory vhosts must have distinct names")
        if any(
            (vhost.permissions is not None) != self.permissions_captured
            or (vhost.topic_permissions is not None) != self.topic_permissions_captured
            for vhost in self.vhosts
        ):
            raise TopologyValidationError("VhostPermissionsInventory rows must agree with section presence")
        object.__setattr__(self, "vhosts", tuple(sorted(self.vhosts, key=lambda vhost: vhost.vhost)))


_UNAVAILABLE = VhostLimit()
_UNSET = VhostLimit(VhostLimitStatus.UNSET)
_CLEARED = VhostLimit(VhostLimitStatus.CLEARED)


def _limit_from_object(value: object, key: str) -> VhostLimit:
    if not isinstance(value, dict):
        return _UNAVAILABLE
    if key not in value:
        return _UNSET
    count = value[key]
    if type(count) is not int:
        return _UNAVAILABLE
    return VhostLimit(_numeric_limit_status(count), count)


def _runtime_limit(value: object, key: str) -> VhostLimit:
    if isinstance(value, (list, dict)) and not value:
        return _CLEARED
    return _limit_from_object(value, key)


def _capture_limit(key: str, *, parameters: list[object], embedded: object, parameters_captured: bool) -> VhostLimit:
    if parameters:
        values = {_runtime_limit(value, key) for value in parameters}
        return values.pop() if len(values) == 1 else _UNAVAILABLE
    if embedded is not None:
        return _limit_from_object(embedded, key)
    return _UNSET if parameters_captured else _UNAVAILABLE


def capture_vhost_permissions(
    response: ClusterDefinitionsResponse, *, resource_vhosts: Iterable[str]
) -> VhostPermissionsInventory:
    """Extract only the captured grants and recognized vhost limit settings."""
    fields = response.model_fields_set
    permissions_captured = "permissions" in fields
    topic_permissions_captured = "topic_permissions" in fields
    parameters_captured = "parameters" in fields
    permissions: dict[str, list[VhostPermission]] = defaultdict(list)
    topics: dict[str, list[VhostTopicPermission]] = defaultdict(list)
    parameters: dict[str, list[object]] = defaultdict(list)
    for grant in response.permissions:
        permissions[grant.vhost].append(VhostPermission(grant.user, grant.configure, grant.write, grant.read))
    for topic in response.topic_permissions:
        topics[topic.vhost].append(VhostTopicPermission(topic.user, topic.exchange, topic.write, topic.read))
    for parameter in response.parameters:
        if parameter.component == "vhost-limits" and parameter.name == "limits":
            parameters[parameter.vhost].append(parameter.value)

    declared = {vhost.name: vhost.limits for vhost in response.vhosts}
    names = declared.keys() | permissions.keys() | topics.keys() | parameters.keys() | set(resource_vhosts)
    rows = []
    for name in names:
        max_queues, max_connections = (
            _capture_limit(
                key, parameters=parameters[name], embedded=declared.get(name), parameters_captured=parameters_captured
            )
            for key in ("max-queues", "max-connections")
        )
        rows.append(
            VhostPermissions(
                name,
                tuple(permissions[name]) if permissions_captured else None,
                tuple(topics[name]) if topic_permissions_captured else None,
                max_queues,
                max_connections,
            )
        )
    return VhostPermissionsInventory(
        vhosts=tuple(rows),
        permissions_captured=permissions_captured,
        topic_permissions_captured=topic_permissions_captured,
    )


def vhost_permissions_inventory(topology: ClusterTopology) -> VhostPermissionsInventory:
    """Read captured metadata or report unavailable evidence for known vhosts."""
    captured = topology.vhost_permissions
    if captured.vhosts or captured.permissions_captured or captured.topic_permissions_captured:
        return captured
    names = {vhost.name for vhost in topology.vhosts} | {node.id.vhost for node in topology.nodes}
    return VhostPermissionsInventory(tuple(VhostPermissions(name) for name in names))
