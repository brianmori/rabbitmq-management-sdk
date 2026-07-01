# rabbitmq_management_sdk/domains/v4/admin/schemas/definitions.py
from __future__ import annotations

from pydantic import Field

from rabbitmq_management_sdk.domains.base import RabbitMQBase

# ---------------------------------------------------------------------------
# Shared sub-models
# ---------------------------------------------------------------------------


class DefinitionUser(RabbitMQBase):
    name: str
    password_hash: str
    hashing_algorithm: str
    tags: list[str]
    limits: dict[str, int] = Field(default_factory=dict)
    """
    Per-user limits introduced in RabbitMQ 3.12.
    e.g. {"max-connections": 100, "max-channels": 200}
    """


class DefinitionVhost(RabbitMQBase):
    name: str
    metadata: DefinitionVhostMetadata | None = None
    limits: list[dict[str, object]] = Field(default_factory=list)
    default_queue_type: str | None = Field(None, alias="default_queue_type")


class DefinitionVhostMetadata(RabbitMQBase):
    description: str = ""
    tags: list[str] = Field(default_factory=list)


class DefinitionPermission(RabbitMQBase):
    user: str
    vhost: str
    configure: str
    write: str
    read: str


class DefinitionTopicPermission(RabbitMQBase):
    user: str
    vhost: str
    exchange: str
    write: str
    read: str


class DefinitionGlobalParameter(RabbitMQBase):
    name: str
    value: str | int | float | bool | dict[str, object] | list[object]


class DefinitionParameter(RabbitMQBase):
    """Vhost-scoped runtime parameter (e.g. a dynamic shovel definition)."""

    component: str
    vhost: str
    name: str
    value: dict[str, object]


class DefinitionPolicy(RabbitMQBase):
    vhost: str
    name: str
    pattern: str
    definition: dict[str, object]
    priority: int = 0
    apply_to: str = Field("all", alias="apply-to")


class DefinitionQueue(RabbitMQBase):
    name: str
    vhost: str
    durable: bool
    auto_delete: bool
    arguments: dict[str, object] = Field(default_factory=dict)


class DefinitionExchange(RabbitMQBase):
    name: str
    vhost: str
    type: str
    durable: bool
    auto_delete: bool
    internal: bool
    arguments: dict[str, object] = Field(default_factory=dict)


class DefinitionBinding(RabbitMQBase):
    source: str
    vhost: str
    destination: str
    destination_type: str = Field(alias="destination_type")
    routing_key: str
    arguments: dict[str, object] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# GET /api/definitions  — cluster-wide export
# ---------------------------------------------------------------------------


class ClusterDefinitionsResponse(RabbitMQBase):
    """
    Response for GET /api/definitions.

    Exports everything except messages: users, vhosts, permissions,
    exchanges, queues, bindings, policies, parameters, global parameters.

    Note: both rabbit_version (legacy) and rabbitmq_version (4.x)
    may be present. Both are optional since the field name changed
    across versions.
    """

    rabbit_version: str | None = None
    rabbitmq_version: str | None = None

    users: list[DefinitionUser] = Field(default_factory=list)
    vhosts: list[DefinitionVhost] = Field(default_factory=list)
    permissions: list[DefinitionPermission] = Field(default_factory=list)
    topic_permissions: list[DefinitionTopicPermission] = Field(default_factory=list)
    global_parameters: list[DefinitionGlobalParameter] = Field(default_factory=list)
    parameters: list[DefinitionParameter] = Field(default_factory=list)
    policies: list[DefinitionPolicy] = Field(default_factory=list)
    queues: list[DefinitionQueue] = Field(default_factory=list)
    exchanges: list[DefinitionExchange] = Field(default_factory=list)
    bindings: list[DefinitionBinding] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# GET /api/definitions/{vhost}  — vhost-scoped export
# ---------------------------------------------------------------------------


class VhostDefinitionsResponse(RabbitMQBase):
    """
    Response for GET /api/definitions/{vhost}.

    Subset of ClusterDefinitionsResponse: omits users, vhosts,
    permissions, topic_permissions, and global_parameters — those
    are cluster-level and cannot be imported via the vhost endpoint.

    POST /api/definitions/{vhost} accepts this same shape.
    """

    rabbit_version: str | None = None
    rabbitmq_version: str | None = None

    parameters: list[DefinitionParameter] = Field(default_factory=list)
    policies: list[DefinitionPolicy] = Field(default_factory=list)
    queues: list[DefinitionQueue] = Field(default_factory=list)
    exchanges: list[DefinitionExchange] = Field(default_factory=list)
    bindings: list[DefinitionBinding] = Field(default_factory=list)
