from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import ClassVar, Literal

from pydantic import Field, JsonValue, model_validator
from typing_extensions import TypedDict

from rabbitmq_management_sdk.resources._argument_map import JsonArgumentEntries, ResponseArgumentMap
from rabbitmq_management_sdk.resources.base import RabbitMQResponse
from rabbitmq_management_sdk.resources.v4.common import (
    DeadLetterStrategy,
    DelayedRetryType,
    OverflowBehaviour,
    QueueLeaderLocator,
)
from rabbitmq_management_sdk.resources.v4.policies.schemas._definition import _PolicyDefinitionMap
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
    ALL_WITH_X = "all-with-x"
    ANY_WITH_X = "any-with-x"


# ---------------------------------------------------------------------------
# Shared sub-models
# ---------------------------------------------------------------------------


class ClusterExportUser(RabbitMQResponse):
    name: str
    password_hash: str
    hashing_algorithm: str
    tags: list[str]
    limits: dict[str, int] = Field(default_factory=dict)
    """
    Per-user limits
    e.g. {"max-connections": 100, "max-channels": 200}
    """


class ClusterExportVhostMetadata(RabbitMQResponse):
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    default_queue_type: str | None = None


class ClusterExportVhost(RabbitMQResponse):
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    metadata: ClusterExportVhostMetadata | None = None
    # Some cluster exports also mirror the vhost's runtime limits here.
    limits: JsonValue = None


class ClusterExportPermission(RabbitMQResponse):
    user: str
    vhost: str
    configure: str
    write: str
    read: str


class ClusterExportTopicPermission(RabbitMQResponse):
    user: str
    vhost: str
    exchange: str
    write: str
    read: str


class ClusterExportGlobalParameter(RabbitMQResponse):
    """Cluster-wide (virtual-host-independent) runtime parameter.

    `value` stays a generic union since its shape depends on `name` and
    covers several unrelated broker-defined parameters; use the typed
    accessor(s) below for the well-known ones this SDK models explicitly.
    The field is required, but explicit JSON null is valid and is retained as
    None. Typed accessors return None for null or incompatible values.
    """

    name: str
    value: str | int | float | bool | dict[str, object] | list[object] | None

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


class ExportPolicySettings(_PolicyDefinitionMap):
    """Captured policy settings, including plugin-defined keys."""

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


class _ExportPolicyBase(RabbitMQResponse):
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
    which lives one level up on the parameter object itself. The broker's
    runtime parameter accepts the full policy scope vocabulary.
    """

    apply_to: OperatorPolicyApplyTo | Literal["all", "exchanges"] = Field(alias="apply-to")


class ClusterExportParameter(RabbitMQResponse):
    """Vhost-scoped runtime parameter (shovels, operator policies, vhost
    limits, federation upstreams, ...). `value` preserves arbitrary JSON since
    its shape depends on `component`: cleared limits can be an empty list,
    and federation upstream sets are arrays of objects. Use the typed
    accessor(s) below for the ones this SDK models explicitly.
    """

    component: str
    vhost: str
    name: str
    value: JsonValue

    def as_operator_policy(self) -> OperatorPolicyParameterValue | None:
        """Typed view of `value` when component == 'operator_policy'."""
        if self.component != "operator_policy":
            return None
        return OperatorPolicyParameterValue.model_validate(self.value)


_QueueArgumentFields = TypedDict(
    "_QueueArgumentFields",
    {
        "x-queue-type": QueueType | None,
        "x-dead-letter-exchange": str | None,
        "x-dead-letter-routing-key": str | None,
        "x-dead-letter-strategy": DeadLetterStrategy | None,
        "x-delivery-limit": int | None,
        "x-delayed-retry-type": DelayedRetryType | None,
        "x-delayed-retry-min": int | None,
        "x-delayed-retry-max": int | None,
        "x-consumer-timeout": int | None,
        "x-consumer-disconnected-timeout": int | None,
        "x-max-length": int | None,
        "x-max-length-bytes": int | None,
        "x-message-ttl": int | None,
        "x-overflow": OverflowBehaviour | None,
        "x-queue-leader-locator": QueueLeaderLocator | None,
        "x-single-active-consumer": bool | None,
        "x-quorum-initial-group-size": int | None,
        "x-quorum-target-group-size": int | None,
    },
    total=False,
)


class _QueueArgumentValues(_QueueArgumentFields, JsonArgumentEntries):
    """Known wire keys plus additional JSON entries."""


class ExportQueueArguments(ResponseArgumentMap[_QueueArgumentValues]):
    """Captured arguments with exact wire keys and typed accessors."""

    _field_aliases: ClassVar[dict[str, str]] = {
        "queue_type": "x-queue-type",
        "dead_letter_exchange": "x-dead-letter-exchange",
        "dead_letter_routing_key": "x-dead-letter-routing-key",
        "dead_letter_strategy": "x-dead-letter-strategy",
        "delivery_limit": "x-delivery-limit",
        "delayed_retry_type": "x-delayed-retry-type",
        "delayed_retry_min": "x-delayed-retry-min",
        "delayed_retry_max": "x-delayed-retry-max",
        "consumer_timeout": "x-consumer-timeout",
        "consumer_disconnected_timeout": "x-consumer-disconnected-timeout",
        "max_length": "x-max-length",
        "max_length_bytes": "x-max-length-bytes",
        "message_ttl": "x-message-ttl",
        "overflow": "x-overflow",
        "queue_leader_locator": "x-queue-leader-locator",
        "single_active_consumer": "x-single-active-consumer",
        "quorum_initial_group_size": "x-quorum-initial-group-size",
        "quorum_target_group_size": "x-quorum-target-group-size",
    }

    @property
    def queue_type(self) -> QueueType | None:
        return self.root.get("x-queue-type")

    @property
    def dead_letter_exchange(self) -> str | None:
        return self.root.get("x-dead-letter-exchange")

    @property
    def dead_letter_routing_key(self) -> str | None:
        return self.root.get("x-dead-letter-routing-key")

    @property
    def dead_letter_strategy(self) -> DeadLetterStrategy | None:
        return self.root.get("x-dead-letter-strategy")

    @property
    def delivery_limit(self) -> int | None:
        return self.root.get("x-delivery-limit")

    @property
    def delayed_retry_type(self) -> DelayedRetryType | None:
        return self.root.get("x-delayed-retry-type")

    @property
    def delayed_retry_min(self) -> int | None:
        return self.root.get("x-delayed-retry-min")

    @property
    def delayed_retry_max(self) -> int | None:
        return self.root.get("x-delayed-retry-max")

    @property
    def consumer_timeout(self) -> int | None:
        return self.root.get("x-consumer-timeout")

    @property
    def consumer_disconnected_timeout(self) -> int | None:
        return self.root.get("x-consumer-disconnected-timeout")

    @property
    def max_length(self) -> int | None:
        return self.root.get("x-max-length")

    @property
    def max_length_bytes(self) -> int | None:
        return self.root.get("x-max-length-bytes")

    @property
    def message_ttl(self) -> int | None:
        return self.root.get("x-message-ttl")

    @property
    def overflow(self) -> OverflowBehaviour | None:
        return self.root.get("x-overflow")

    @property
    def queue_leader_locator(self) -> QueueLeaderLocator | None:
        return self.root.get("x-queue-leader-locator")

    @property
    def single_active_consumer(self) -> bool | None:
        return self.root.get("x-single-active-consumer")

    @property
    def quorum_initial_group_size(self) -> int | None:
        return self.root.get("x-quorum-initial-group-size")

    @property
    def quorum_target_group_size(self) -> int | None:
        return self.root.get("x-quorum-target-group-size")

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


class ClusterExportQueue(RabbitMQResponse):
    name: str
    vhost: str
    durable: bool
    auto_delete: bool
    arguments: ExportQueueArguments = Field(default_factory=ExportQueueArguments)


_ExchangeArgumentFields = TypedDict(
    "_ExchangeArgumentFields",
    {
        "alternate-exchange": str | None,
    },
    total=False,
)


class _ExchangeArgumentValues(_ExchangeArgumentFields, JsonArgumentEntries):
    """Known wire keys plus additional JSON entries."""


class ExportExchangeArguments(ResponseArgumentMap[_ExchangeArgumentValues]):
    """Captured arguments with exact wire keys and typed accessors."""

    _field_aliases: ClassVar[dict[str, str]] = {
        "alternate_exchange": "alternate-exchange",
    }

    @property
    def alternate_exchange(self) -> str | None:
        return self.root.get("alternate-exchange")


class ClusterExportExchange(RabbitMQResponse):
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


_BindingArgumentFields = TypedDict(
    "_BindingArgumentFields",
    {
        "x-match": HeadersMatchMode | None,
    },
    total=False,
)


class _BindingArgumentValues(_BindingArgumentFields, JsonArgumentEntries):
    """Known wire keys plus additional JSON entries."""


class ExportBindingArguments(ResponseArgumentMap[_BindingArgumentValues]):
    """Captured arguments with exact wire keys and typed accessors."""

    _field_aliases: ClassVar[dict[str, str]] = {
        "x_match": "x-match",
    }

    @property
    def x_match(self) -> HeadersMatchMode | None:
        return self.root.get("x-match")


class ClusterExportBinding(RabbitMQResponse):
    source: str
    vhost: str
    destination: str
    destination_type: Literal["queue", "exchange"] = Field(alias="destination_type")
    routing_key: str
    arguments: ExportBindingArguments = Field(default_factory=ExportBindingArguments)


# ---------------------------------------------------------------------------
# GET /api/definitions  — cluster-wide export
# ---------------------------------------------------------------------------


class ClusterDefinitionsResponse(RabbitMQResponse):
    """Response for GET /api/definitions.

    Exports everything except messages: users, vhosts, permissions,
    exchanges, queues, bindings, policies, parameters, global parameters.
    At least one definitions section must be supplied, even when empty.
    Older and manual definitions may omit version and format metadata.
    """

    rabbit_version: str | None = None
    rabbitmq_version: str | None = None
    product_name: str | None = None
    product_version: str | None = None
    rabbitmq_definition_format: Literal["cluster"] | None = None
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

    @model_validator(mode="before")
    @classmethod
    def _validate_envelope(cls, data: object) -> object:
        if not isinstance(data, Mapping):
            return data
        if "error" in data:
            raise ValueError("Expected cluster definitions, received an error response")
        if "original_vhost_name" in data:
            raise ValueError("Expected cluster definitions, received a single-vhost export")
        sections = {
            "users",
            "vhosts",
            "permissions",
            "topic_permissions",
            "global_parameters",
            "parameters",
            "policies",
            "queues",
            "exchanges",
            "bindings",
        }
        if not sections.intersection(data):
            raise ValueError("Cluster definitions must contain at least one definitions section")
        return data

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


class VhostExportQueue(RabbitMQResponse):
    """Queue entry in a vhost-scoped export. No 'vhost' field."""

    name: str
    durable: bool
    auto_delete: bool
    arguments: ExportQueueArguments = Field(default_factory=ExportQueueArguments)


class VhostExportExchange(RabbitMQResponse):
    name: str
    type: str
    durable: bool
    auto_delete: bool
    internal: bool = False
    arguments: ExportExchangeArguments = Field(default_factory=ExportExchangeArguments)


class VhostExportBinding(RabbitMQResponse):
    source: str
    destination: str
    destination_type: str
    routing_key: str
    arguments: ExportBindingArguments = Field(default_factory=ExportBindingArguments)


class VhostExportPolicy(RabbitMQResponse):
    name: str
    pattern: str
    definition: ExportPolicySettings
    priority: int = 0
    apply_to: PolicyApplyTo = Field(PolicyApplyTo.ALL, alias="apply-to")


class VhostExportParameter(RabbitMQResponse):
    """Includes plugin parameters (shovel, federation) and a synthetic
    'vhost-limits' parameter that echoes the vhost's connection/queue
    limits. Values preserve their component-specific JSON shape.
    """

    component: str
    name: str
    value: JsonValue

    def is_shovel(self) -> bool:
        return self.component == "shovel"

    def as_operator_policy(self) -> OperatorPolicyParameterValue | None:
        """Typed view of `value` when component == 'operator_policy'."""
        if self.component != "operator_policy":
            return None
        return OperatorPolicyParameterValue.model_validate(self.value)


class VhostExportMetadata(RabbitMQResponse):
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    default_queue_type: str | None = None


_LimitArgumentFields = TypedDict(
    "_LimitArgumentFields",
    {
        "max-connections": int | None,
        "max-queues": int | None,
    },
    total=False,
)


class _LimitArgumentValues(_LimitArgumentFields, JsonArgumentEntries):
    """Known wire keys plus additional JSON entries."""


class VhostExportLimits(ResponseArgumentMap[_LimitArgumentValues]):
    """Limits captured from a vhost export, including future limit keys."""

    _field_aliases: ClassVar[dict[str, str]] = {
        "max_connections": "max-connections",
        "max_queues": "max-queues",
    }

    @property
    def max_connections(self) -> int | None:
        return self.root.get("max-connections")

    @property
    def max_queues(self) -> int | None:
        return self.root.get("max-queues")

    @model_validator(mode="before")
    @classmethod
    def _normalize_empty_list(cls, data: object) -> object:
        if isinstance(data, list):
            if len(data) == 0:
                return {}
            raise ValueError(f"Expected a dict of limits or an empty list, got a non-empty list: {data}")
        return data


class VhostDefinitionsResponse(RabbitMQResponse):
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
