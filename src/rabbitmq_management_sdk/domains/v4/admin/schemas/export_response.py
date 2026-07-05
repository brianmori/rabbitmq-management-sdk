from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from rabbitmq_management_sdk.domains.base import RabbitMQBase

# ---------------------------------------------------------------------------
# Enums — broker-core elements only.
#
# Plugin-extensible (exchange types, policy *definition* keys such
# as federation's `federation-upstream[-set]`) are stored as a `str` / `dict[str, object]`
# with `extra="allow"` instead. See DefinitionPolicyDefinition below.
# ---------------------------------------------------------------------------


class PolicyApplyTo(StrEnum):
    """`apply-to` for regular (user) policies. Includes exchanges."""

    QUEUES = "queues"
    CLASSIC_QUEUES = "classic_queues"
    QUORUM_QUEUES = "quorum_queues"
    STREAMS = "streams"
    EXCHANGES = "exchanges"
    ALL = "all"  # management UI label: "Exchanges and queues"


class OperatorPolicyApplyTo(StrEnum):
    """`apply-to` for operator policies. Queue/stream targets only.
    """

    QUEUES = "queues"
    CLASSIC_QUEUES = "classic_queues"
    QUORUM_QUEUES = "quorum_queues"
    STREAMS = "streams"


class DeadLetterStrategy(StrEnum):
    AT_MOST_ONCE = "at-most-once"
    AT_LEAST_ONCE = "at-least-once"


class OverflowBehaviour(StrEnum):
    DROP_HEAD = "drop-head"
    REJECT_PUBLISH = "reject-publish"
    REJECT_PUBLISH_DLX = "reject-publish-dlx"


class QueueLeaderLocator(StrEnum):
    CLIENT_LOCAL = "client-local"  # default
    BALANCED = "balanced"


class QueueType(StrEnum):
    CLASSIC = "classic"
    QUORUM = "quorum"
    STREAM = "stream"

class HeadersMatchMode(StrEnum):
    ALL = "all"  # default if x-match is omitted
    ANY = "any"

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
    Per-user limits
    e.g. {"max-connections": 100, "max-channels": 200}
    """


class DefinitionVhostMetadata(RabbitMQBase):
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    default_queue_type: str | None = None


class DefinitionVhost(RabbitMQBase):
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    metadata: DefinitionVhostMetadata | None = None


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


class DefinitionPolicyDefinition(RabbitMQBase):
    """The `definition` object of a policy (or an operator-policy value).

    Known keys are typed. `extra="allow"` preserves anything else losslessly —
    plugin-contributed keys (e.g. federation's `federation-upstream` /
    `federation-upstream-set`) and any newer broker keys this SDK doesn't
    implement yet.
    """

    model_config = RabbitMQBase.model_config | {"extra": "allow"}

    max_length: int | None = Field(None, alias="max-length")
    max_length_bytes: int | None = Field(None, alias="max-length-bytes")
    message_ttl: int | None = Field(None, alias="message-ttl")
    expires: int | None = None
    overflow: OverflowBehaviour | None = None
    dead_letter_exchange: str | None = Field(None, alias="dead-letter-exchange")
    dead_letter_routing_key: str | None = Field(None, alias="dead-letter-routing-key")
    dead_letter_strategy: DeadLetterStrategy | None = Field(None, alias="dead-letter-strategy")
    delivery_limit: int | None = Field(None, alias="delivery-limit")
    consumer_timeout: int | None = Field(None, alias="consumer-timeout")
    queue_leader_locator: QueueLeaderLocator | None = Field(None, alias="queue-leader-locator")
    max_age: str | None = Field(None, alias="max-age")  # streams, e.g. "1h"
    stream_filter_size_bytes: int | None = Field(None, alias="stream-filter-size-bytes")
    max_in_memory_length: int | None = Field(None, alias="max-in-memory-length")  # quorum
    max_in_memory_bytes: int | None = Field(None, alias="max-in-memory-bytes")  # quorum
    target_group_size: int | None = Field(None, alias="target-group-size")  # quorum
    alternate_exchange: str | None = Field(None, alias="alternate-exchange")  # exchanges

    @property
    def effective_dead_letter_strategy(self) -> DeadLetterStrategy | None:
        """RabbitMQ silently falls back to at-most-once dead lettering unless
        overflow is explicitly reject-publish. This mirrors that broker-side
        behavior as a read-only computed value rather than raising an error, since
        this model parses definitions the broker already accepted — a
        hard-failing validator here would break parsing of legitimate
        exports.
        """
        if (
            self.dead_letter_strategy == DeadLetterStrategy.AT_LEAST_ONCE
            and self.overflow != OverflowBehaviour.REJECT_PUBLISH
        ):
            return DeadLetterStrategy.AT_MOST_ONCE
        return self.dead_letter_strategy


class PolicyDefinitionBase(RabbitMQBase):
    """Shared shape between a regular policy and an operator-policy value:
    pattern / definition / priority. `apply-to` differs between the two
    (see PolicyApplyTo vs. OperatorPolicyApplyTo), so it's declared on each
    subclass rather than here.
    """

    pattern: str
    definition: DefinitionPolicyDefinition
    priority: int = 0


class DefinitionPolicy(PolicyDefinitionBase):
    vhost: str
    name: str
    apply_to: PolicyApplyTo = Field(PolicyApplyTo.ALL, alias="apply-to")


class OperatorPolicyValue(PolicyDefinitionBase):
    """The `value` payload of a runtime parameter where component ==
    'operator_policy'. Same shape as DefinitionPolicy minus vhost/name,
    which lives one level up on the parameter object itself — but apply-to
    is restricted to queue/stream targets; operator policies can't target
    exchanges.
    """

    apply_to: OperatorPolicyApplyTo = Field(alias="apply-to")


class DefinitionParameter(RabbitMQBase):
    """Vhost-scoped runtime parameter (shovels, operator policies, vhost
    limits, federation upstreams, ...). `value` stays a generic dict since
    its shape depends entirely on `component` and covers several unrelated
    payload types; use the typed accessor(s) below for the ones this SDK
    models explicitly.
    """

    component: str
    vhost: str
    name: str
    value: dict[str, object]

    def as_operator_policy(self) -> OperatorPolicyValue | None:
        """Typed view of `value` when component == 'operator_policy'."""
        if self.component != "operator_policy":
            return None
        return OperatorPolicyValue.model_validate(self.value)


class DefinitionQueueArguments(RabbitMQBase):
    model_config = RabbitMQBase.model_config | {"extra": "allow"}

    queue_type: QueueType | None = Field(None, alias="x-queue-type")
    dead_letter_exchange: str | None = Field(None, alias="x-dead-letter-exchange")
    dead_letter_routing_key: str | None = Field(None, alias="x-dead-letter-routing-key")
    dead_letter_strategy: DeadLetterStrategy | None = Field(None, alias="x-dead-letter-strategy")
    delivery_limit: int | None = Field(None, alias="x-delivery-limit")
    max_length: int | None = Field(None, alias="x-max-length")
    max_length_bytes: int | None = Field(None, alias="x-max-length-bytes")
    message_ttl: int | None = Field(None, alias="x-message-ttl")
    overflow: OverflowBehaviour | None = Field(None, alias="x-overflow")
    queue_leader_locator: QueueLeaderLocator | None = Field(None, alias="x-queue-leader-locator")
    single_active_consumer: bool | None = Field(None, alias="x-single-active-consumer")
    quorum_initial_group_size: int | None = Field(None, alias="x-quorum-initial-group-size")
    quorum_target_group_size: int | None = Field(None, alias="x-quorum-target-group-size")

    @property
    def effective_dead_letter_strategy(self) -> DeadLetterStrategy | None:
        """Same broker rule as DefinitionPolicyDefinition: at-least-once
        silently falls back to at-most-once unless overflow is explicitly
        reject-publish. Applies whether the pair was set via a policy or
        directly as queue-declare arguments.
        """
        if (
            self.dead_letter_strategy == DeadLetterStrategy.AT_LEAST_ONCE
            and self.overflow != OverflowBehaviour.REJECT_PUBLISH
        ):
            return DeadLetterStrategy.AT_MOST_ONCE
        return self.dead_letter_strategy

class DefinitionQueue(RabbitMQBase):
    name: str
    vhost: str
    durable: bool
    auto_delete: bool
    arguments: DefinitionQueueArguments = Field(default_factory=DefinitionQueueArguments)


class DefinitionExchangeArguments(RabbitMQBase):
    """Strongly typed arguments for exchanges.

    extra="allow" ensures that plugin arguments
    (e.g., `x-delayed-type` for the delayed message exchange plugin,
    or `hash-header` for consistent hashing) are preserved.
    """

    model_config = RabbitMQBase.model_config | {"extra": "allow"}

    alternate_exchange: str | None = Field(None, alias="alternate-exchange")


class DefinitionExchange(RabbitMQBase):
    name: str
    vhost: str
    type: str
    """
    Open string, not an enum: plugin-contributed exchange types
    (x-consistent-hash, x-delayed-message, ...) must still validate.
    """
    durable: bool
    auto_delete: bool
    internal: bool
    arguments: DefinitionExchangeArguments = Field(default_factory=DefinitionExchangeArguments)


class DefinitionBindingArguments(RabbitMQBase):
    model_config = RabbitMQBase.model_config | {"extra": "allow"}

    # Used in headers exchanges
    x_match: HeadersMatchMode | None = Field(None, alias="x-match")

class DefinitionBinding(RabbitMQBase):
    source: str
    vhost: str
    destination: str
    destination_type: Literal["queue", "exchange"] = Field(alias="destination_type")
    routing_key: str
    arguments: DefinitionBindingArguments = Field(default_factory=DefinitionBindingArguments)


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
    product_name: str | None = None
    product_version: str | None = None
    rabbitmq_definition_format: str | None = None
    """
    Distinguishes a full cluster export ("cluster") from a single-vhost
    export via GET /api/definitions/{vhost} (different top-level shape).
    """
    original_cluster_name: str | None = None
    explanation: str | None = None

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
    """Queue entry in a vhost-scoped export. No 'vhost' field."""
    name: str
    durable: bool
    auto_delete: bool
    arguments: DefinitionQueueArguments = Field(default_factory=DefinitionQueueArguments)


class VhostDefinitionsExchange(RabbitMQBase):
    name: str
    type: str
    durable: bool
    auto_delete: bool
    internal: bool
    arguments: DefinitionExchangeArguments = Field(default_factory=DefinitionExchangeArguments)


class VhostDefinitionsBinding(RabbitMQBase):
    source: str
    destination: str
    destination_type: str
    routing_key: str
    arguments: DefinitionBindingArguments = Field(default_factory=DefinitionBindingArguments)


class VhostDefinitionsPolicy(RabbitMQBase):
    name: str
    pattern: str
    definition: dict[str, object]
    priority: int = 0
    apply_to: PolicyApplyTo = Field(PolicyApplyTo.ALL, alias="apply-to")



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

    def as_operator_policy(self) -> OperatorPolicyValue | None:
        """Typed view of `value` when component == 'operator_policy'."""
        if self.component != "operator_policy":
            return None
        return OperatorPolicyValue.model_validate(self.value)

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
