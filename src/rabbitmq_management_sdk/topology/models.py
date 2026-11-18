"""Define immutable graph values for RabbitMQ topology analysis.

Graph algorithms operate on :class:`NodeId` and :class:`TopologyEdge`.
:class:`ExchangeNode`, :class:`QueueNode`, and :class:`ShovelNode` form an
attribute layer keyed by node identity. An edge records a configured possible
message hop; it does not guarantee that a particular message will traverse it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING

from rabbitmq_management_sdk.exceptions import TopologyValidationError
from rabbitmq_management_sdk.topology.vhost_permissions import VhostPermissionsInventory

if TYPE_CHECKING:
    from collections.abc import Mapping

_MAX_NETWORK_PORT = 65535


class NodeKind(StrEnum):
    """Kinds of resources represented as topology graph nodes."""

    EXCHANGE = "exchange"
    QUEUE = "queue"
    SHOVEL = "shovel"


def _require_instance(value: object, expected: type[object], field_name: str) -> None:
    """Raise the public validation error used by all topology value objects."""
    if not isinstance(value, expected):
        raise TopologyValidationError(f"{field_name} must be {expected.__name__}, got {value!r}")


def _require_optional_instance(value: object, expected: type[object], field_name: str) -> None:
    """Validate an optional topology value when it is present."""
    if value is not None:
        _require_instance(value, expected, field_name)


@dataclass(frozen=True, slots=True)
class NodeId:
    """Identify one resource node within a cluster topology.

    ``kind`` is part of the identity because RabbitMQ keeps queues and
    exchanges in separate namespaces, allowing both to share a name within one
    virtual host.

    ``cluster_id`` prevents a later multi-cluster composition from silently
    merging otherwise identical resource coordinates. It contains the
    broker-generated ``internal_cluster_id`` when the export supplies one.
    Independently exported nodes with ``cluster_id=None`` are not globally
    unique and must not be merged by resource coordinates alone.
    """

    vhost: str
    name: str
    kind: NodeKind
    cluster_id: str | None = None

    def __post_init__(self) -> None:
        """Validate every component of the node identity."""
        _require_instance(self.vhost, str, "NodeId.vhost")
        _require_instance(self.name, str, "NodeId.name")
        _require_instance(self.kind, NodeKind, "NodeId.kind")
        _require_optional_instance(self.cluster_id, str, "NodeId.cluster_id")

    def __str__(self) -> str:
        """Return a compact cluster-scoped resource identifier."""
        cluster = f"{self.cluster_id}:" if self.cluster_id is not None else ""
        return f"{self.kind}:{cluster}[{self.vhost}]/{self.name}"  # useful when the vhost is empty


class EdgeKind(StrEnum):
    """Ways a message can make one configured, directed topology hop."""

    BINDING = "binding"
    """An exchange has a binding hop to a queue or another exchange.

    The edge comes from a binding in the definitions export. Whether a message
    matches that binding depends on its routing key, headers, and exchange
    behavior.
    """

    DEAD_LETTER = "dead_letter"
    """A queue has a possible dead-letter hop to its configured exchange.

    The setting may come from a queue argument or the selected regular user
    policy. That source is configuration provenance, not a separate edge kind;
    the hop occurs only when RabbitMQ dead-letters a message.
    """

    ALTERNATE_EXCHANGE = "alternate_exchange"
    """An exchange has a possible hop to its alternate exchange.

    The setting may come from an exchange argument or the selected regular
    user policy. The hop occurs only when the original exchange cannot route a
    message.
    """

    SHOVEL = "shovel"
    """A confirmed shovel hop involving a queue or exchange.

    A shovel is represented as two independent possible hops:

    * source resource -> shovel, when the source is confirmed local and fixed;
    * shovel -> destination resource, when the destination is confirmed local
      and fixed.

    Either endpoint may be remote, unresolved, or dynamically addressed, so a
    shovel can contribute zero, one, or two graph edges.
    """


@dataclass(frozen=True, slots=True)
class ExchangeNode:
    """Represent an exchange and the attributes retained by the graph.

    Attributes:
        id: Cluster-scoped exchange identity.
        exchange_type: Open string containing a built-in, plugin-provided, or
            operator-defined exchange type.
        internal: Whether publishers are prohibited from publishing directly
            to the exchange.
        durable: Whether the exchange survives broker restarts.
    """

    id: NodeId
    exchange_type: str
    internal: bool
    durable: bool

    def __post_init__(self) -> None:
        """Validate exchange attributes and node kind."""
        _require_instance(self.id, NodeId, "ExchangeNode.id")
        _require_instance(self.exchange_type, str, "ExchangeNode.exchange_type")
        _require_instance(self.internal, bool, "ExchangeNode.internal")
        _require_instance(self.durable, bool, "ExchangeNode.durable")
        if self.id.kind != NodeKind.EXCHANGE:
            raise TopologyValidationError(f"ExchangeNode.id.kind must be NodeKind.EXCHANGE, got {self.id.kind!r}")

    @property
    def is_default(self) -> bool:
        """Whether this is the virtual host's nameless default exchange."""
        return self.id.name == ""


@dataclass(frozen=True, slots=True)
class ConfigurationEvidence:
    """One captured or derived input to a quorum configuration setting.

    Attributes:
        source: Origin of the value; no credentials or arbitrary JSON are stored.
        key: Exact argument/policy key, or a documented derivation name.
        value: Captured scalar, including an explicitly captured null.
        policy_name: Selected policy name when known.
    """

    source: ConfigurationSource
    key: str
    value: str | int | None
    policy_name: str | None = None

    def __post_init__(self) -> None:
        """Validate immutable scalar evidence."""
        _require_instance(self.source, ConfigurationSource, "ConfigurationEvidence.source")
        _require_instance(self.key, str, "ConfigurationEvidence.key")
        _require_optional_instance(self.policy_name, str, "ConfigurationEvidence.policy_name")
        if self.value is not None and type(self.value) not in (str, int):
            raise TopologyValidationError("ConfigurationEvidence.value must be str, int, or None")


class ConfigurationSource(StrEnum):
    """Origins retained by quorum configuration resolution."""

    QUEUE_ARGUMENT = "queue_argument"
    REGULAR_POLICY = "regular_policy"
    OPERATOR_POLICY = "operator_policy"
    EFFECTIVE_POLICY = "effective_policy"
    BROKER_DEFAULT = "broker_default"
    DERIVED = "derived"


class ConfigurationStatus(StrEnum):
    """Distinguish resolved, absent, uncertain, and inapplicable settings."""

    KNOWN = "known"
    UNSET = "unset"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class ConfigurationUncertainty(StrEnum):
    """Reasons captured configuration cannot resolve a setting."""

    CONFIGURATION_NOT_CAPTURED = "configuration_not_captured"
    BROKER_VERSION_NOT_CAPTURED = "broker_version_not_captured"
    UNSUPPORTED_BROKER_VERSION = "unsupported_broker_version"
    POLICY_SELECTION_NOT_CAPTURED = "policy_selection_not_captured"
    OPERATOR_POLICY_SELECTION_NOT_CAPTURED = "operator_policy_selection_not_captured"
    BROKER_DEFAULT_NOT_CAPTURED = "broker_default_not_captured"
    CAPTURED_NULL = "captured_null"
    INVALID_VALUE = "invalid_value"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    ARGUMENTS_NOT_CAPTURED = "arguments_not_captured"
    POLICY_DEFINITIONS_NOT_CAPTURED = "policy_definitions_not_captured"
    OPERATOR_POLICY_DEFINITIONS_NOT_CAPTURED = "operator_policy_definitions_not_captured"
    UNSUPPORTED_QUEUE_TYPE = "unsupported_queue_type"


@dataclass(frozen=True, slots=True)
class ConfigurationValue[T: str | int]:
    """A typed configuration result with canonical evidence and uncertainty.

    ``unset`` means captured inputs establish absence. ``unknown`` must not be
    interpreted as disabled. A known negative delivery limit means unlimited.
    """

    value: T | None = None
    status: ConfigurationStatus = ConfigurationStatus.UNKNOWN
    evidence: tuple[ConfigurationEvidence, ...] = ()
    uncertainties: tuple[ConfigurationUncertainty, ...] = (ConfigurationUncertainty.CONFIGURATION_NOT_CAPTURED,)

    def __post_init__(self) -> None:
        """Validate result state and canonicalize its immutable evidence."""
        _require_instance(self.status, ConfigurationStatus, "ConfigurationValue.status")
        if self.value is not None and type(self.value) not in (str, int):
            raise TopologyValidationError("ConfigurationValue.value must be str, int, or None")
        if (self.status == ConfigurationStatus.KNOWN) != (self.value is not None):
            raise TopologyValidationError("ConfigurationValue known status requires a value")
        if not isinstance(self.evidence, tuple) or any(
            not isinstance(item, ConfigurationEvidence) for item in self.evidence
        ):
            raise TopologyValidationError("ConfigurationValue.evidence must be a tuple of ConfigurationEvidence")
        if not isinstance(self.uncertainties, tuple) or any(
            not isinstance(item, ConfigurationUncertainty) for item in self.uncertainties
        ):
            raise TopologyValidationError(
                "ConfigurationValue.uncertainties must be a tuple of ConfigurationUncertainty"
            )
        if (self.status == ConfigurationStatus.UNKNOWN) != bool(self.uncertainties):
            raise TopologyValidationError("ConfigurationValue unknown status requires uncertainties exclusively")
        object.__setattr__(
            self,
            "evidence",
            tuple(
                sorted(
                    set(self.evidence),
                    key=lambda item: (item.source.value, item.key, repr(item.value), item.policy_name or ""),
                )
            ),
        )
        object.__setattr__(self, "uncertainties", tuple(sorted(set(self.uncertainties), key=lambda item: item.value)))


@dataclass(frozen=True, slots=True)
class QuorumDeliveryConfiguration:
    """Queue-level delivery configuration supported for captured 4.2/4.3.

    Timeouts and retry bounds use milliseconds. Consumer-level overrides and
    runtime activity are outside this value. Missing global timeout settings
    stay unknown. The configured retry selector is retained separately from
    its effective selector, which can be disabled by an absent minimum.
    """

    broker_version: str | None = None
    configured_retry_type: ConfigurationValue[str] = field(default_factory=ConfigurationValue)
    delayed_retry_type: ConfigurationValue[str] = field(default_factory=ConfigurationValue)
    delayed_retry_min: ConfigurationValue[int] = field(default_factory=ConfigurationValue)
    delayed_retry_max: ConfigurationValue[int] = field(default_factory=ConfigurationValue)
    delivery_limit: ConfigurationValue[int] = field(default_factory=ConfigurationValue)
    delivery_count_basis: ConfigurationValue[str] = field(default_factory=ConfigurationValue)
    consumer_timeout: ConfigurationValue[int] = field(default_factory=ConfigurationValue)
    consumer_disconnected_timeout: ConfigurationValue[int] = field(default_factory=ConfigurationValue)
    dead_letter_exchange: ConfigurationValue[str] = field(default_factory=ConfigurationValue)
    dead_letter_strategy: ConfigurationValue[str] = field(default_factory=ConfigurationValue)
    overflow: ConfigurationValue[str] = field(default_factory=ConfigurationValue)
    effective_dead_letter_strategy: ConfigurationValue[str] = field(default_factory=ConfigurationValue)

    def __post_init__(self) -> None:
        """Validate setting types independently of queue identity."""
        _require_optional_instance(self.broker_version, str, "QuorumDeliveryConfiguration.broker_version")
        for name in (
            "configured_retry_type",
            "delayed_retry_type",
            "delivery_count_basis",
            "dead_letter_exchange",
            "dead_letter_strategy",
            "overflow",
            "effective_dead_letter_strategy",
            "delayed_retry_min",
            "delayed_retry_max",
            "delivery_limit",
            "consumer_timeout",
            "consumer_disconnected_timeout",
        ):
            setting = getattr(self, name)
            _require_instance(setting, ConfigurationValue, f"QuorumDeliveryConfiguration.{name}")
            expected = (
                int
                if name
                in {
                    "delayed_retry_min",
                    "delayed_retry_max",
                    "delivery_limit",
                    "consumer_timeout",
                    "consumer_disconnected_timeout",
                }
                else str
            )
            if setting.value is not None and type(setting.value) is not expected:
                raise TopologyValidationError(f"QuorumDeliveryConfiguration.{name} has an invalid value type")
            if type(setting.value) is int and name != "delivery_limit":
                minimum = 1 if name in {"delayed_retry_min", "delayed_retry_max"} else 0
                if setting.value < minimum:
                    raise TopologyValidationError(f"QuorumDeliveryConfiguration.{name} has an invalid duration")
        domains = {
            "configured_retry_type": {"disabled", "all", "failed", "returned"},
            "delayed_retry_type": {"disabled", "all", "failed", "returned"},
            "delivery_count_basis": {"acquired", "failed"},
            "dead_letter_strategy": {"at-most-once", "at-least-once"},
            "overflow": {"drop-head", "reject-publish"},
            "effective_dead_letter_strategy": {"disabled", "at-most-once", "at-least-once"},
        }
        for name, domain in domains.items():
            if (value := getattr(self, name).value) is not None and value not in domain:
                raise TopologyValidationError(f"QuorumDeliveryConfiguration.{name} has an invalid value")


@dataclass(frozen=True, slots=True)
class QuorumPlacement:
    """Management API placement evidence for one quorum queue.

    Broker node names are exact and ordered lexically. An empty or missing
    member list cannot establish group size. An explicit empty online list
    records zero reported online members. The leader can be absent or offline.
    """

    members: tuple[str, ...] | None = None
    leader: str | None = None
    online_members: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        """Validate captured identities and canonicalize lists."""
        for field_name in ("members", "online_members"):
            names = getattr(self, field_name)
            if names is None:
                continue
            if not isinstance(names, tuple) or any(not isinstance(name, str) or not name for name in names):
                raise TopologyValidationError(f"QuorumPlacement.{field_name} must be a tuple of nonempty strings")
            if len(set(names)) != len(names):
                raise TopologyValidationError(f"QuorumPlacement.{field_name} cannot contain duplicate names")
            object.__setattr__(self, field_name, tuple(sorted(names)))
        if self.leader is not None and (not isinstance(self.leader, str) or not self.leader):
            raise TopologyValidationError("QuorumPlacement.leader must be a nonempty string or None")
        if self.members:
            members = set(self.members)
            if self.leader is not None and self.leader not in members:
                raise TopologyValidationError("QuorumPlacement.leader must belong to members")
            if not members.issuperset(self.online_members or ()):
                raise TopologyValidationError("QuorumPlacement.online_members must belong to members")


@dataclass(frozen=True, slots=True)
class QueueLimitsConfiguration:
    """Captured queue limits and stream retention, with separate units.

    The length limit counts messages. Size limits are in bytes, and time
    values are in milliseconds. Stream settings describe log retention.
    """

    broker_version: str | None = None
    max_length: ConfigurationValue[int] = field(default_factory=ConfigurationValue)
    max_length_bytes: ConfigurationValue[int] = field(default_factory=ConfigurationValue)
    overflow: ConfigurationValue[str] = field(default_factory=ConfigurationValue)
    message_ttl_ms: ConfigurationValue[int] = field(default_factory=ConfigurationValue)
    queue_expiry_ms: ConfigurationValue[int] = field(default_factory=ConfigurationValue)
    stream_max_age_ms: ConfigurationValue[int] = field(default_factory=ConfigurationValue)
    stream_max_length_bytes: ConfigurationValue[int] = field(default_factory=ConfigurationValue)

    def __post_init__(self) -> None:
        """Check the scalar type of each frozen configuration value."""
        _require_optional_instance(self.broker_version, str, "QueueLimitsConfiguration.broker_version")
        for name in (
            "max_length",
            "max_length_bytes",
            "overflow",
            "message_ttl_ms",
            "queue_expiry_ms",
            "stream_max_age_ms",
            "stream_max_length_bytes",
        ):
            setting = getattr(self, name)
            _require_instance(setting, ConfigurationValue, f"QueueLimitsConfiguration.{name}")
            if setting.value is not None and type(setting.value) is not (str if name == "overflow" else int):
                raise TopologyValidationError(f"QueueLimitsConfiguration.{name} has the wrong scalar type")


@dataclass(frozen=True, slots=True)
class QueueNode:
    """Represent a queue and the attributes retained by the graph.

    Attributes:
        id: Cluster-scoped queue identity.
        queue_type: Open string containing the queue implementation type, such
            as ``classic``, ``quorum``, or ``stream``.
        durable: Whether the queue survives broker restarts.
        replication_factor: Captured quorum membership count, including the
            leader. ``None`` means unknown or a non-quorum queue. This is not
            the number of online members or a physical disk-write estimate.
        quorum_delivery: Frozen queue-level delivery evidence for quorum queues.
            ``None`` means this configuration was not captured.
        quorum_placement: Frozen member, leader, and online evidence for
            quorum queues. Nonempty membership supplies the replication factor.
        queue_limits: Frozen limits and retention evidence, if captured.
        exclusive: Observed queue exclusivity. ``None`` means it was not
            established by a compatible queue observation.
    """

    id: NodeId
    queue_type: str
    durable: bool
    replication_factor: int | None = None
    quorum_delivery: QuorumDeliveryConfiguration | None = None
    quorum_placement: QuorumPlacement | None = None
    queue_limits: QueueLimitsConfiguration | None = None
    exclusive: bool | None = None

    def __post_init__(self) -> None:
        """Validate queue attributes and node kind."""
        _require_instance(self.id, NodeId, "QueueNode.id")
        _require_instance(self.queue_type, str, "QueueNode.queue_type")
        _require_instance(self.durable, bool, "QueueNode.durable")
        _require_optional_instance(self.exclusive, bool, "QueueNode.exclusive")
        _require_optional_instance(self.quorum_delivery, QuorumDeliveryConfiguration, "QueueNode.quorum_delivery")
        if self.quorum_delivery is not None and self.queue_type != "quorum":
            raise TopologyValidationError("QueueNode.quorum_delivery requires a quorum queue")
        self._validate_limits()
        if self.id.kind != NodeKind.QUEUE:
            raise TopologyValidationError(f"QueueNode.id.kind must be NodeKind.QUEUE, got {self.id.kind!r}")
        if self.replication_factor is not None:
            if type(self.replication_factor) is not int or self.replication_factor < 1:
                raise TopologyValidationError("QueueNode.replication_factor must be a positive int or None")
            if self.queue_type != "quorum":
                raise TopologyValidationError("QueueNode.replication_factor requires a quorum queue")
        _require_optional_instance(self.quorum_placement, QuorumPlacement, "QueueNode.quorum_placement")
        if self.quorum_placement is not None:
            if self.queue_type != "quorum":
                raise TopologyValidationError("QueueNode.quorum_placement requires a quorum queue")
            if self.quorum_placement.members:
                member_count = len(self.quorum_placement.members)
                if self.replication_factor is not None and self.replication_factor != member_count:
                    raise TopologyValidationError("QueueNode.replication_factor must match captured membership")
                object.__setattr__(self, "replication_factor", member_count)
            elif self.replication_factor is not None:
                reported_members = set(self.quorum_placement.online_members or ())
                if self.quorum_placement.leader is not None:
                    reported_members.add(self.quorum_placement.leader)
                if len(reported_members) > self.replication_factor:
                    raise TopologyValidationError("QueueNode.replication_factor cannot be below reported membership")

    def _validate_limits(self) -> None:
        """Keep the quorum overflow evidence consistent on manually built nodes."""
        _require_optional_instance(self.queue_limits, QueueLimitsConfiguration, "QueueNode.queue_limits")
        if self.queue_type == "quorum" and self.queue_limits is not None:
            overflow = self.quorum_delivery.overflow if self.quorum_delivery is not None else ConfigurationValue()
            if self.queue_limits.overflow != overflow:
                raise TopologyValidationError("QueueNode.queue_limits.overflow must match quorum delivery evidence")


class ShovelAckMode(StrEnum):
    """Source acknowledgement mode captured for a dynamic shovel."""

    ON_CONFIRM = "on-confirm"
    ON_PUBLISH = "on-publish"
    NO_ACK = "no-ack"
    UNKNOWN = "unknown"


class ShovelAckSource(StrEnum):
    """How the acknowledgement mode was established."""

    EXPLICIT = "explicit"
    BROKER_DEFAULT = "broker_default"
    INVALID_VALUE = "invalid_value"
    UNKNOWN = "unknown"


class ShovelAckTiming(StrEnum):
    """Configured acknowledgement timing relative to the destination."""

    AFTER_DESTINATION_CONFIRM = "after_destination_confirm"
    AFTER_DESTINATION_PUBLISH = "after_destination_publish"
    AUTOMATIC = "automatic"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ShovelAcknowledgement:
    """Credential-free source acknowledgement evidence."""

    mode: ShovelAckMode = ShovelAckMode.UNKNOWN
    source: ShovelAckSource = ShovelAckSource.UNKNOWN

    def __post_init__(self) -> None:
        """Validate the mode and its origin without echoing supplied values."""
        if not isinstance(self.mode, ShovelAckMode) or not isinstance(self.source, ShovelAckSource):
            raise TopologyValidationError("ShovelAcknowledgement requires a ShovelAckMode and ShovelAckSource")
        if (self.mode != ShovelAckMode.UNKNOWN) != (
            self.source in {ShovelAckSource.EXPLICIT, ShovelAckSource.BROKER_DEFAULT}
        ):
            raise TopologyValidationError("ShovelAcknowledgement mode and source must agree")
        if self.source == ShovelAckSource.BROKER_DEFAULT and self.mode != ShovelAckMode.ON_CONFIRM:
            raise TopologyValidationError("ShovelAcknowledgement broker default must be on-confirm")

    @property
    def timing(self) -> ShovelAckTiming:
        """Return the source acknowledgement behavior implied by the mode."""
        return {
            ShovelAckMode.ON_CONFIRM: ShovelAckTiming.AFTER_DESTINATION_CONFIRM,
            ShovelAckMode.ON_PUBLISH: ShovelAckTiming.AFTER_DESTINATION_PUBLISH,
            ShovelAckMode.NO_ACK: ShovelAckTiming.AUTOMATIC,
            ShovelAckMode.UNKNOWN: ShovelAckTiming.UNKNOWN,
        }[self.mode]


class ShovelTransport(StrEnum):
    """Configured transport evidence for a candidate or endpoint."""

    LOCAL = "local"
    NETWORK_AMQP = "network_amqp"
    NETWORK_AMQPS = "network_amqps"
    MIXED = "mixed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ShovelEndpointTransport:
    """Transport classifications in captured candidate order, with duplicates."""

    candidates: tuple[ShovelTransport, ...] = ()

    def __post_init__(self) -> None:
        """Validate per-candidate facts without echoing supplied values."""
        if not isinstance(self.candidates, tuple) or any(
            not isinstance(candidate, ShovelTransport) or candidate == ShovelTransport.MIXED
            for candidate in self.candidates
        ):
            raise TopologyValidationError("ShovelEndpointTransport requires individual candidate classifications")

    @property
    def kind(self) -> ShovelTransport:
        """Summarize the endpoint's configured transports."""
        known = set(self.candidates) - {ShovelTransport.UNKNOWN}
        if len(known) > 1:
            return ShovelTransport.MIXED
        if ShovelTransport.UNKNOWN in self.candidates:
            return ShovelTransport.UNKNOWN
        return next(iter(known), ShovelTransport.UNKNOWN)


@dataclass(frozen=True, slots=True)
class EndpointAuthority:
    """Credential-free network authority for one AMQP endpoint candidate.

    ``host=None`` represents a hostless URI. Scheme and host casing are
    canonicalized here so independently parsed equivalent authorities compare
    equally. The port is normalized to the scheme's default when the URI
    omits it. Interpret transport using the separate endpoint evidence.
    """

    scheme: str
    host: str | None
    port: int

    def __post_init__(self) -> None:
        """Validate the normalized, credential-free authority."""
        _require_instance(self.scheme, str, "EndpointAuthority.scheme")
        _require_optional_instance(self.host, str, "EndpointAuthority.host")
        if not isinstance(self.port, int) or isinstance(self.port, bool):
            raise TopologyValidationError(f"EndpointAuthority.port must be int, got {self.port!r}")
        scheme = self.scheme.casefold()
        host = self.host.casefold() if self.host is not None else None
        if not scheme:
            raise TopologyValidationError("EndpointAuthority.scheme must not be empty")
        if host == "":
            raise TopologyValidationError("EndpointAuthority.host must be a non-empty str or None")
        if not 1 <= self.port <= _MAX_NETWORK_PORT:
            raise TopologyValidationError(f"EndpointAuthority.port must be between 1 and 65535, got {self.port!r}")
        object.__setattr__(self, "scheme", scheme)
        object.__setattr__(self, "host", host)


@dataclass(frozen=True, slots=True)
class ResourceEndpoint:
    """Normalized evidence for one side of a shovel.

    This value deliberately retains enough credential-free connection and
    resource information for a later offline multi-cluster composer. It does
    not assign a remote ``cluster_id``: only explicit operator-supplied
    mappings may do that.

    ``authorities=None`` means the connection candidates could not be
    normalized. An empty tuple is reserved for the in-cluster ``local``
    protocol. A non-empty tuple contains every candidate from the shovel's
    failover list. ``is_confirmed_local`` is true only when both its vhost and
    normalized authority evidence are known and admit the endpoint to the
    current cluster graph; false means unconfirmed, not necessarily remote.
    ``transport`` is independent of locality and can retain individual
    classifications when authority normalization fails for the full set.
    """

    protocol: str | None
    authorities: tuple[EndpointAuthority, ...] | None
    vhost: str | None
    resource_name: str | None
    resource_kind: NodeKind | None
    routing_key: str | None
    is_confirmed_local: bool = False
    transport: ShovelEndpointTransport = ShovelEndpointTransport()

    def __post_init__(self) -> None:
        """Validate normalized endpoint evidence."""
        _require_optional_instance(self.protocol, str, "ResourceEndpoint.protocol")
        _require_optional_instance(self.vhost, str, "ResourceEndpoint.vhost")
        _require_optional_instance(self.resource_name, str, "ResourceEndpoint.resource_name")
        _require_optional_instance(self.resource_kind, NodeKind, "ResourceEndpoint.resource_kind")
        _require_optional_instance(self.routing_key, str, "ResourceEndpoint.routing_key")
        _require_instance(self.is_confirmed_local, bool, "ResourceEndpoint.is_confirmed_local")
        if not isinstance(self.transport, ShovelEndpointTransport):
            raise TopologyValidationError("ResourceEndpoint.transport must be ShovelEndpointTransport")
        if self.authorities is not None:
            if not isinstance(self.authorities, tuple):
                raise TopologyValidationError(
                    f"ResourceEndpoint.authorities must be a tuple or None, got {self.authorities!r}"
                )
            for authority in self.authorities:
                _require_instance(authority, EndpointAuthority, "ResourceEndpoint.authorities")
            if not self.authorities and self.protocol != "local":
                raise TopologyValidationError("ResourceEndpoint.authorities may be empty only when protocol is 'local'")
        if (self.resource_name is None) != (self.resource_kind is None):
            raise TopologyValidationError(
                "ResourceEndpoint.resource_name and resource_kind must either both be present or both be None"
            )
        if self.resource_kind not in {None, NodeKind.EXCHANGE, NodeKind.QUEUE}:
            raise TopologyValidationError(
                "ResourceEndpoint.resource_kind must be NodeKind.EXCHANGE, NodeKind.QUEUE, or None, "
                f"got {self.resource_kind!r}"
            )
        if self.routing_key is not None and self.resource_kind != NodeKind.EXCHANGE:
            raise TopologyValidationError("ResourceEndpoint.routing_key is only valid for exchange endpoints")
        if self.is_confirmed_local and self.vhost is None:
            raise TopologyValidationError("ResourceEndpoint.is_confirmed_local requires a known ResourceEndpoint.vhost")
        if self.is_confirmed_local and self.authorities is None:
            raise TopologyValidationError(
                "ResourceEndpoint.is_confirmed_local requires normalized ResourceEndpoint.authorities"
            )


@dataclass(frozen=True, slots=True)
class ShovelNode:
    """Represent a shovel and its normalized endpoint evidence.

    Attributes:
        id: Cluster-scoped shovel identity.
        source: Normalized source connection and resource evidence.
        destination: Normalized destination connection and resource evidence.
        acknowledgement: Source acknowledgement mode and its provenance.
    """

    id: NodeId
    source: ResourceEndpoint
    destination: ResourceEndpoint
    acknowledgement: ShovelAcknowledgement = ShovelAcknowledgement()

    def __post_init__(self) -> None:
        """Validate endpoint metadata and shovel node identity."""
        _require_instance(self.id, NodeId, "ShovelNode.id")
        _require_instance(self.source, ResourceEndpoint, "ShovelNode.source")
        _require_instance(self.destination, ResourceEndpoint, "ShovelNode.destination")
        if not isinstance(self.acknowledgement, ShovelAcknowledgement):
            raise TopologyValidationError("ShovelNode.acknowledgement must be ShovelAcknowledgement")
        if self.id.kind != NodeKind.SHOVEL:
            raise TopologyValidationError(f"ShovelNode.id.kind must be NodeKind.SHOVEL, got {self.id.kind!r}")

    @property
    def is_cross_vhost(self) -> bool | None:
        """Return whether the shovel crosses a virtual-host boundary.

        ``None`` means at least one endpoint virtual host could not be resolved.
        The value is derived from the source and destination endpoints so it
        cannot drift out of sync with their metadata.
        """
        if self.source.vhost is None or self.destination.vhost is None:
            return None
        return self.source.vhost != self.destination.vhost

    @property
    def has_unconfirmed_endpoint(self) -> bool:
        """Whether either endpoint has not been confirmed as local.

        ``False`` on either endpoint's ``is_confirmed_local`` field means
        normalized URI and vhost evidence plus the supplied host mapping could
        not establish locality. It is not proof that the endpoint belongs to
        another cluster.
        """
        return not self.source.is_confirmed_local or not self.destination.is_confirmed_local


TopologyNode = ExchangeNode | QueueNode | ShovelNode
"""Any resource node contained by :class:`ClusterTopology`."""


@dataclass(frozen=True, slots=True)
class TopologyEdge:
    """Represent one configured possible message hop in the topology graph.

    The edge records configuration evidence, not a runtime-delivery guarantee.
    Whether a message follows it can depend on routing keys, headers,
    dead-letter conditions, or shovel runtime state.

    Every non-shovel relationship is local to one cluster and virtual host.
    Shovel relationships may cross either boundary when a later federated
    topology composition supplies explicit remote-cluster evidence.

    Attributes:
        source: Identity of the edge's origin.
        target: Identity of the edge's destination.
        kind: Relationship represented by the edge.
        routing_key: Captured key for a binding, configured dead-letter
            override, or exchange shovel endpoint. ``None`` means no fixed key
            was captured or the field is inapplicable; it does not imply that
            runtime messages have no routing key. An empty string remains a
            distinct, meaningful RabbitMQ routing key.
        arguments: Canonical, key-sorted JSON for binding arguments. It is
            ``None`` for a binding with no supplied arguments and for every
            other edge kind. Explicit JSON ``null`` values are preserved.
    """

    source: NodeId
    target: NodeId
    kind: EdgeKind
    routing_key: str | None = None
    arguments: str | None = None

    def __post_init__(self) -> None:
        """Validate endpoint kinds and edge-specific optional fields."""
        _require_instance(self.source, NodeId, "TopologyEdge.source")
        _require_instance(self.target, NodeId, "TopologyEdge.target")
        _require_instance(self.kind, EdgeKind, "TopologyEdge.kind")
        _require_optional_instance(self.routing_key, str, "TopologyEdge.routing_key")
        _require_optional_instance(self.arguments, str, "TopologyEdge.arguments")

        source_kind, target_kind = self.source.kind, self.target.kind
        valid_kinds_by_edge = {
            EdgeKind.BINDING: (source_kind == NodeKind.EXCHANGE and target_kind in {NodeKind.EXCHANGE, NodeKind.QUEUE}),
            EdgeKind.DEAD_LETTER: source_kind == NodeKind.QUEUE and target_kind == NodeKind.EXCHANGE,
            EdgeKind.ALTERNATE_EXCHANGE: source_kind == NodeKind.EXCHANGE and target_kind == NodeKind.EXCHANGE,
            EdgeKind.SHOVEL: (
                (source_kind in {NodeKind.EXCHANGE, NodeKind.QUEUE} and target_kind == NodeKind.SHOVEL)
                or (source_kind == NodeKind.SHOVEL and target_kind in {NodeKind.EXCHANGE, NodeKind.QUEUE})
            ),
        }
        if not valid_kinds_by_edge[self.kind]:
            raise TopologyValidationError(
                f"TopologyEdge {self.kind.value!r} has invalid endpoint kinds "
                f"{source_kind.value!r} -> {target_kind.value!r}"
            )
        if self.kind != EdgeKind.SHOVEL and self.source.cluster_id != self.target.cluster_id:
            raise TopologyValidationError(
                f"TopologyEdge {self.kind.value!r} must stay within one cluster, got "
                f"{self.source.cluster_id!r} -> {self.target.cluster_id!r}"
            )
        if self.kind != EdgeKind.SHOVEL and self.source.vhost != self.target.vhost:
            raise TopologyValidationError(
                f"TopologyEdge {self.kind.value!r} must stay within one vhost, got "
                f"{self.source.vhost!r} -> {self.target.vhost!r}"
            )
        if self.kind != EdgeKind.BINDING and self.arguments is not None:
            raise TopologyValidationError("TopologyEdge.arguments is only valid for binding edges")
        if self.kind == EdgeKind.ALTERNATE_EXCHANGE and self.routing_key is not None:
            raise TopologyValidationError("TopologyEdge.routing_key must be None for alternate-exchange edges")
        if (
            self.kind == EdgeKind.SHOVEL
            and self.routing_key is not None
            and NodeKind.QUEUE in {source_kind, target_kind}
        ):
            raise TopologyValidationError("TopologyEdge.routing_key is invalid for shovel edges connected to queues")

    def __str__(self) -> str:
        """Return a compact representation of the directed relationship."""
        return f"{self.source} -> {self.target} [{self.kind}]"


@dataclass(frozen=True, slots=True)
class VirtualHostMetadata:
    """Captured metadata for a virtual host within one topology.

    This is not a routing node. ``default_queue_type`` describes the captured
    vhost default, independently of the types of its existing queues. ``None``
    means the default was not supplied; it does not imply ``classic``.
    """

    name: str
    default_queue_type: str | None = None

    def __post_init__(self) -> None:
        """Validate retained vhost metadata without restricting type names."""
        _require_instance(self.name, str, "VirtualHostMetadata.name")
        _require_optional_instance(self.default_queue_type, str, "VirtualHostMetadata.default_queue_type")


@dataclass(frozen=True, slots=True)
class ClusterTopology:
    """An immutable normalized graph for one captured cluster configuration.

    The graph is built from a definitions export and optional resource
    observations, and normalizes the supported topology evidence in those
    inputs. They may come from several Management API calls and need not form
    an atomic broker snapshot. The graph is not live and does not guarantee that
    messages will traverse its edges.

    ``cluster_id`` is the broker-generated ``internal_cluster_id`` and provides
    stable topology identity when present. Older or manually constructed
    exports may omit it; independently acquired topologies with
    ``cluster_id=None`` must not be merged by resource identity alone.
    ``cluster_name`` retains the broker-assigned name as evidence for later
    offline multi-cluster composition. ``cluster_label`` is presentation
    metadata and never participates in equality or hashing.

    ``vhosts`` retains declared virtual hosts, including those without queues,
    without adding routing nodes. Omitted metadata remains unknown even when
    resource identities contain a vhost name.

    ``vhost_permissions`` retains projected permission declarations and
    vhost count limits. It defaults to an inventory without captured evidence.

    The typed node collections retain resource attributes. ``nodes``,
    ``all_node_ids``, and ``nodes_by_id`` provide cached unified graph views.
    """

    exchanges: frozenset[ExchangeNode]
    queues: frozenset[QueueNode]
    shovels: frozenset[ShovelNode]
    edges: frozenset[TopologyEdge]
    cluster_id: str | None = None
    cluster_name: str | None = None
    cluster_label: str | None = field(default=None, compare=False, hash=False)
    vhosts: frozenset[VirtualHostMetadata] = frozenset()
    vhost_permissions: VhostPermissionsInventory = field(default_factory=VhostPermissionsInventory)
    _nodes: frozenset[TopologyNode] = field(init=False, repr=False, compare=False, hash=False)
    _all_node_ids: frozenset[NodeId] = field(init=False, repr=False, compare=False, hash=False)
    _nodes_by_id: Mapping[NodeId, TopologyNode] = field(init=False, repr=False, compare=False, hash=False)

    def __post_init__(self) -> None:
        """Validate graph consistency and construct immutable lookup views."""
        _require_optional_instance(self.cluster_id, str, "ClusterTopology.cluster_id")
        _require_optional_instance(self.cluster_name, str, "ClusterTopology.cluster_name")
        _require_optional_instance(self.cluster_label, str, "ClusterTopology.cluster_label")
        _require_instance(self.vhost_permissions, VhostPermissionsInventory, "ClusterTopology.vhost_permissions")
        collections = (
            ("ClusterTopology.exchanges", self.exchanges, ExchangeNode),
            ("ClusterTopology.queues", self.queues, QueueNode),
            ("ClusterTopology.shovels", self.shovels, ShovelNode),
            ("ClusterTopology.edges", self.edges, TopologyEdge),
            ("ClusterTopology.vhosts", self.vhosts, VirtualHostMetadata),
        )
        for field_name, values, value_type in collections:
            if not isinstance(values, frozenset):
                raise TopologyValidationError(f"{field_name} must be a frozenset, got {values!r}")
            for value in values:
                _require_instance(value, value_type, field_name)

        if len({vhost.name for vhost in self.vhosts}) != len(self.vhosts):
            raise TopologyValidationError("conflicting virtual host metadata for the same name")

        seen: dict[NodeId, ExchangeNode | QueueNode | ShovelNode] = {}
        all_nodes: tuple[TopologyNode, ...] = (*self.exchanges, *self.queues, *self.shovels)
        for node in all_nodes:
            if node.id.cluster_id != self.cluster_id:
                raise TopologyValidationError(
                    f"node {node.id} belongs to cluster {node.id.cluster_id!r}, "
                    f"not topology cluster {self.cluster_id!r}"
                )
            prior = seen.get(node.id)
            if prior is not None and prior != node:
                raise TopologyValidationError(f"conflicting node data for {node.id}: {prior!r} vs {node!r}")
            seen[node.id] = node

        for edge in self.edges:
            if edge.source.cluster_id != self.cluster_id or edge.target.cluster_id != self.cluster_id:
                raise TopologyValidationError(
                    f"edge {edge} contains a node outside topology cluster {self.cluster_id!r}"
                )

        nodes = frozenset(all_nodes)
        object.__setattr__(self, "_nodes", nodes)
        object.__setattr__(self, "_all_node_ids", frozenset(seen))
        object.__setattr__(self, "_nodes_by_id", MappingProxyType(dict(seen)))

    def __reduce__(
        self,
    ) -> tuple[
        type[ClusterTopology],
        tuple[
            frozenset[ExchangeNode],
            frozenset[QueueNode],
            frozenset[ShovelNode],
            frozenset[TopologyEdge],
            str | None,
            str | None,
            str | None,
            frozenset[VirtualHostMetadata],
            VhostPermissionsInventory,
        ],
    ]:
        """Serialize canonical graph data and rebuild derived caches on load."""
        return (
            type(self),
            (
                self.exchanges,
                self.queues,
                self.shovels,
                self.edges,
                self.cluster_id,
                self.cluster_name,
                self.cluster_label,
                self.vhosts,
                self.vhost_permissions,
            ),
        )

    @property
    def all_node_ids(self) -> frozenset[NodeId]:
        """Return the precomputed identities of every contained graph node."""
        return self._all_node_ids

    @property
    def nodes(self) -> frozenset[TopologyNode]:
        """Return every resource node contained in this immutable graph."""
        return self._nodes

    @property
    def nodes_by_id(self) -> Mapping[NodeId, TopologyNode]:
        """Precomputed immutable mapping with O(1) node lookup."""
        return self._nodes_by_id

    def dangling_edges(self) -> frozenset[TopologyEdge]:
        """Return edges whose source or target is not contained in the graph.

        RabbitMQ definitions can reference resources absent from the captured
        topology. This method reports that structural fact without inferring
        whether the resource exists elsewhere or will be declared later.
        """
        known = self.all_node_ids
        return frozenset(e for e in self.edges if e.source not in known or e.target not in known)
