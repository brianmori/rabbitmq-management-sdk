from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field, model_validator

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
    """Response for GET /api/definitions.

    Exports everything except messages: users, vhosts, permissions,
    exchanges, queues, bindings, policies, parameters, global parameters.
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
# GET /api/definitions/{vhost} — vhost-scoped export
# ---------------------------------------------------------------------------


class VhostDefinitionsQueue(RabbitMQBase):
    """Queue entry in a vhost-scoped export. No 'vhost' field — implied by the response envelope."""

    name: str
    durable: bool
    auto_delete: bool
    arguments: dict[str, object] = Field(default_factory=dict)


class VhostDefinitionsExchange(RabbitMQBase):
    name: str
    type: str
    durable: bool
    auto_delete: bool
    internal: bool
    arguments: dict[str, object] = Field(default_factory=dict)


class VhostDefinitionsBinding(RabbitMQBase):
    source: str
    destination: str
    destination_type: str
    routing_key: str
    arguments: dict[str, object] = Field(default_factory=dict)


class VhostDefinitionsPolicy(RabbitMQBase):
    name: str
    pattern: str
    definition: dict[str, object]
    priority: int = 0
    apply_to: str = Field("all", alias="apply-to")


class VhostDefinitionsParameter(RabbitMQBase):
    """Includes plugin parameters (shovel, federation) and a synthetic
    'vhost-limits' parameter that echoes the vhost's connection/queue
    limits.
    """

    component: str
    name: str
    value: dict[str, object]

    def is_shovel(self) -> bool:
        return self.component == "shovel"


class VhostMetadata(RabbitMQBase):
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    default_queue_type: str | None = None


class VhostLimits(RabbitMQBase):
    """Extra allowed: limit keys are broker-defined and may grow."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")
    max_connections: int | None = Field(None, alias="max-connections")
    max_queues: int | None = Field(None, alias="max-queues")

    @model_validator(mode="before")
    @classmethod
    def _normalize_empty_list(cls, data: object) -> object:
        if isinstance(data, list):
            if len(data) == 0:
                return {}
            raise ValueError(f"Expected a dict of limits or an empty list, got a non-empty list: {data}")
        return data


class VhostDefinitionsResponse(RabbitMQBase):
    """GET /api/definitions/{vhost} — vhost-scoped export."""

    rabbit_version: str
    rabbitmq_version: str
    product_name: str
    product_version: str
    rabbitmq_definition_format: Literal["single_virtual_host"]
    original_vhost_name: str
    explanation: str
    metadata: VhostMetadata
    description: str = ""
    limits: VhostLimits = Field(default_factory=VhostLimits)

    parameters: list[VhostDefinitionsParameter] = Field(default_factory=list)
    policies: list[VhostDefinitionsPolicy] = Field(default_factory=list)
    queues: list[VhostDefinitionsQueue] = Field(default_factory=list)
    exchanges: list[VhostDefinitionsExchange] = Field(default_factory=list)
    bindings: list[VhostDefinitionsBinding] = Field(default_factory=list)
