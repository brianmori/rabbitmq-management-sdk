from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from rabbitmq_management_sdk.resources.base import RabbitMQBase
from rabbitmq_management_sdk.resources.v4.common import (
    DeadLetterStrategy,
    OverflowBehaviour,
    QueueLeaderLocator,
)
from rabbitmq_management_sdk.resources.v4.policies.schemas.common import (
    OperatorPolicyApplyTo,
    PolicyApplyTo,
)

# ---------------------------------------------------------------------------
# Enums — broker-core elements only.
#
# Plugin-extensible (exchange types, policy *definition* keys such
# as federation's `federation-upstream[-set]`) are stored as a `str` / `dict[str, object]`
# with `extra="allow"` instead. See ExportPolicySettings below.
# ---------------------------------------------------------------------------


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


class ClusterExportUser(RabbitMQBase):
    name: str
    password_hash: str
    hashing_algorithm: str
    tags: list[str]
    limits: dict[str, int] = Field(default_factory=dict)
    """
    Per-user limits
    e.g. {"max-connections": 100, "max-channels": 200}
    """


class ClusterExportVhostMetadata(RabbitMQBase):
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    default_queue_type: str | None = None


class ClusterExportVhost(RabbitMQBase):
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    metadata: ClusterExportVhostMetadata | None = None


class ClusterExportPermission(RabbitMQBase):
    user: str
    vhost: str
    configure: str
    write: str
    read: str


class ClusterExportTopicPermission(RabbitMQBase):
    user: str
    vhost: str
    exchange: str
    write: str
    read: str


class ClusterExportGlobalParameter(RabbitMQBase):
    """Cluster-wide (virtual-host-independent) runtime parameter.

    `value` stays a generic union since its shape depends on `name` and
    covers several unrelated broker-defined parameters; use the typed
    accessor(s) below for the well-known ones this SDK models explicitly.
    """

    name: str
    value: str | int | float | bool | dict[str, object] | list[object]

    def as_cluster_name(self) -> str | None:
        """Typed view of `value` when name == 'cluster_name'.

        This is the same value RabbitMQ displays in the management UI and
        exposes via `GET /api/global-parameters/cluster_name`. It also
        appears redundantly as the top-level `original_cluster_name` field
        on the cluster-wide export, which is a snapshot captured at export
        time so the broker can detect a cluster-name mismatch on import.

        Returns:
            The cluster name, or None if this parameter isn't `cluster_name`
            or its value isn't a string.
        """
        if self.name != "cluster_name" or not isinstance(self.value, str):
            return None
        return self.value

    def as_cluster_tags(self) -> dict[str, str] | None:
        """Typed view of `value` when name == 'cluster_tags'.

        Cluster tags are arbitrary operator-defined key/value pairs (e.g.
        environment, region) used to attach deployment-specific
        information to a cluster. They're configured via `cluster_tags.<key>`
        entries in rabbitmq.conf, so values are always strings.

        Returns:
            The tag mapping, or None if this parameter isn't `cluster_tags`
            or its value isn't a dict.
        """
        if self.name != "cluster_tags" or not isinstance(self.value, dict):
            return None
        return {str(k): str(v) for k, v in self.value.items()}

    def as_internal_cluster_id(self) -> str | None:
        """Typed view of `value` when name == 'internal_cluster_id'.

        Broker-generated, stable identifier for the cluster (distinct from
        the operator-assigned `cluster_name`).

        Returns:
            The internal cluster ID, or None if this parameter isn't
            `internal_cluster_id` or its value isn't a string.
        """
        if self.name != "internal_cluster_id" or not isinstance(self.value, str):
            return None
        return self.value


class ExportPolicySettings(RabbitMQBase):
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
    max_age: str | None = Field(None, alias="max-age")
    stream_filter_size_bytes: int | None = Field(None, alias="stream-filter-size-bytes")
    max_in_memory_length: int | None = Field(None, alias="max-in-memory-length")
    max_in_memory_bytes: int | None = Field(None, alias="max-in-memory-bytes")
    target_group_size: int | None = Field(None, alias="target-group-size")
    alternate_exchange: str | None = Field(None, alias="alternate-exchange")

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


class _ExportPolicyBase(RabbitMQBase):
    """Shared shape between a regular policy and an operator-policy value:
    pattern / definition / priority. `apply-to` differs between the two
    (see PolicyApplyTo vs. OperatorPolicyApplyTo), so it's declared on each
    subclass rather than here.
    """

    pattern: str
    definition: ExportPolicySettings
    priority: int = 0


class ClusterExportPolicy(_ExportPolicyBase):
    vhost: str
    name: str
    apply_to: PolicyApplyTo = Field(PolicyApplyTo.ALL, alias="apply-to")


class OperatorPolicyParameterValue(_ExportPolicyBase):
    """The `value` payload of a runtime parameter where component ==
    'operator_policy'. Same shape as ClusterExportPolicy minus vhost/name,
    which lives one level up on the parameter object itself — but apply-to
    is restricted to queue/stream targets; operator policies can't target
    exchanges.
    """

    apply_to: OperatorPolicyApplyTo = Field(alias="apply-to")


class ClusterExportParameter(RabbitMQBase):
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

    def as_operator_policy(self) -> OperatorPolicyParameterValue | None:
        """Typed view of `value` when component == 'operator_policy'."""
        if self.component != "operator_policy":
            return None
        return OperatorPolicyParameterValue.model_validate(self.value)


class ExportQueueArguments(RabbitMQBase):
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
        """Same broker rule as ExportPolicySettings: at-least-once
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


class ClusterExportQueue(RabbitMQBase):
    name: str
    vhost: str
    durable: bool
    auto_delete: bool
    arguments: ExportQueueArguments = Field(default_factory=ExportQueueArguments)


class ExportExchangeArguments(RabbitMQBase):
    """Strongly typed arguments for exchanges.

    extra="allow" ensures that plugin arguments
    (e.g., `x-delayed-type` for the delayed message exchange plugin,
    or `hash-header` for consistent hashing) are preserved.
    """

    model_config = RabbitMQBase.model_config | {"extra": "allow"}

    alternate_exchange: str | None = Field(None, alias="alternate-exchange")


class ClusterExportExchange(RabbitMQBase):
    name: str
    vhost: str
    type: str
    """
    Open string, not an enum: plugin-contributed exchange types
    (x-consistent-hash, x-delayed-message, ...) must still validate.
    """
    durable: bool
    auto_delete: bool
    internal: bool = False
    arguments: ExportExchangeArguments = Field(default_factory=ExportExchangeArguments)


class ExportBindingArguments(RabbitMQBase):
    model_config = RabbitMQBase.model_config | {"extra": "allow"}

    # Used in headers exchanges
    x_match: HeadersMatchMode | None = Field(None, alias="x-match")


class ClusterExportBinding(RabbitMQBase):
    source: str
    vhost: str
    destination: str
    destination_type: Literal["queue", "exchange"] = Field(alias="destination_type")
    routing_key: str
    arguments: ExportBindingArguments = Field(default_factory=ExportBindingArguments)


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

    users: list[ClusterExportUser] = Field(default_factory=list)
    vhosts: list[ClusterExportVhost] = Field(default_factory=list)
    permissions: list[ClusterExportPermission] = Field(default_factory=list)
    topic_permissions: list[ClusterExportTopicPermission] = Field(default_factory=list)
    global_parameters: list[ClusterExportGlobalParameter] = Field(default_factory=list)
    parameters: list[ClusterExportParameter] = Field(default_factory=list)
    policies: list[ClusterExportPolicy] = Field(default_factory=list)
    queues: list[ClusterExportQueue] = Field(default_factory=list)
    exchanges: list[ClusterExportExchange] = Field(default_factory=list)
    bindings: list[ClusterExportBinding] = Field(default_factory=list)

    @property
    def cluster_name(self) -> str | None:
        """Lookup of the well-known `cluster_name` entry in `global_parameters`.

        Returns:
            The cluster name, or None if no such global parameter is present.
        """
        for gp in self.global_parameters:
            if (name := gp.as_cluster_name()) is not None:
                return name
        return None

    @property
    def cluster_tags(self) -> dict[str, str] | None:
        """Lookup of the well-known `cluster_tags` entry in `global_parameters`.

        Returns:
            The cluster tag mapping, or None if no such global parameter is
            present.
        """
        for gp in self.global_parameters:
            if (tags := gp.as_cluster_tags()) is not None:
                return tags
        return None

    @property
    def internal_cluster_id(self) -> str | None:
        """Lookup of the well-known `internal_cluster_id` entry in `global_parameters`.

        Returns:
            The internal cluster ID, or None if no such global parameter is
            present.
        """
        for gp in self.global_parameters:
            if (cluster_id := gp.as_internal_cluster_id()) is not None:
                return cluster_id
        return None


# ---------------------------------------------------------------------------
# GET /api/definitions/{vhost} — vhost-scoped export
# ---------------------------------------------------------------------------


class VhostExportQueue(RabbitMQBase):
    """Queue entry in a vhost-scoped export. No 'vhost' field."""

    name: str
    durable: bool
    auto_delete: bool
    arguments: ExportQueueArguments = Field(default_factory=ExportQueueArguments)


class VhostExportExchange(RabbitMQBase):
    name: str
    type: str
    durable: bool
    auto_delete: bool
    internal: bool = False
    arguments: ExportExchangeArguments = Field(default_factory=ExportExchangeArguments)


class VhostExportBinding(RabbitMQBase):
    source: str
    destination: str
    destination_type: str
    routing_key: str
    arguments: ExportBindingArguments = Field(default_factory=ExportBindingArguments)


class VhostExportPolicy(RabbitMQBase):
    name: str
    pattern: str
    definition: ExportPolicySettings
    priority: int = 0
    apply_to: PolicyApplyTo = Field(PolicyApplyTo.ALL, alias="apply-to")


class VhostExportParameter(RabbitMQBase):
    """Includes plugin parameters (shovel, federation) and a synthetic
    'vhost-limits' parameter that echoes the vhost's connection/queue
    limits.
    """

    component: str
    name: str
    value: dict[str, object]

    def is_shovel(self) -> bool:
        return self.component == "shovel"

    def as_operator_policy(self) -> OperatorPolicyParameterValue | None:
        """Typed view of `value` when component == 'operator_policy'."""
        if self.component != "operator_policy":
            return None
        return OperatorPolicyParameterValue.model_validate(self.value)


class VhostExportMetadata(RabbitMQBase):
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    default_queue_type: str | None = None


class VhostExportLimits(RabbitMQBase):
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
    metadata: VhostExportMetadata
    description: str = ""
    limits: VhostExportLimits = Field(default_factory=VhostExportLimits)

    parameters: list[VhostExportParameter] = Field(default_factory=list)
    policies: list[VhostExportPolicy] = Field(default_factory=list)
    queues: list[VhostExportQueue] = Field(default_factory=list)
    exchanges: list[VhostExportExchange] = Field(default_factory=list)
    bindings: list[VhostExportBinding] = Field(default_factory=list)
