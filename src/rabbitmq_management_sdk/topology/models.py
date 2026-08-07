"""Define immutable graph values for RabbitMQ topology analysis.

Graph algorithms operate on :class:`NodeId` and :class:`TopologyEdge`.
:class:`ExchangeNode`, :class:`QueueNode`, and :class:`ShovelNode` form an
attribute layer keyed by node identity. An edge records a configured possible
message hop; it does not guarantee that a particular message will traverse it.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

from rabbitmq_management_sdk.exceptions import TopologyValidationError

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
class QueueNode:
    """Represent a queue and the attributes retained by the graph.

    Attributes:
        id: Cluster-scoped queue identity.
        queue_type: Open string containing the queue implementation type, such
            as ``classic``, ``quorum``, or ``stream``.
        durable: Whether the queue survives broker restarts.
    """

    id: NodeId
    queue_type: str
    durable: bool

    def __post_init__(self) -> None:
        """Validate queue attributes and node kind."""
        _require_instance(self.id, NodeId, "QueueNode.id")
        _require_instance(self.queue_type, str, "QueueNode.queue_type")
        _require_instance(self.durable, bool, "QueueNode.durable")
        if self.id.kind != NodeKind.QUEUE:
            raise TopologyValidationError(f"QueueNode.id.kind must be NodeKind.QUEUE, got {self.id.kind!r}")


@dataclass(frozen=True, slots=True)
class EndpointAuthority:
    """Credential-free network authority for one AMQP endpoint candidate.

    ``host=None`` represents a hostless URI, which RabbitMQ interprets relative
    to the shovel's hosting cluster. Scheme and host casing are canonicalized
    here so independently parsed equivalent authorities compare equally. The
    port is normalized to the scheme's default when the URI omits it.
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
    """

    protocol: str | None
    authorities: tuple[EndpointAuthority, ...] | None
    vhost: str | None
    resource_name: str | None
    resource_kind: NodeKind | None
    routing_key: str | None
    is_confirmed_local: bool = False

    def __post_init__(self) -> None:
        """Validate normalized endpoint evidence."""
        _require_optional_instance(self.protocol, str, "ResourceEndpoint.protocol")
        _require_optional_instance(self.vhost, str, "ResourceEndpoint.vhost")
        _require_optional_instance(self.resource_name, str, "ResourceEndpoint.resource_name")
        _require_optional_instance(self.resource_kind, NodeKind, "ResourceEndpoint.resource_kind")
        _require_optional_instance(self.routing_key, str, "ResourceEndpoint.routing_key")
        _require_instance(self.is_confirmed_local, bool, "ResourceEndpoint.is_confirmed_local")
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
    """

    id: NodeId
    source: ResourceEndpoint
    destination: ResourceEndpoint

    def __post_init__(self) -> None:
        """Validate endpoint metadata and shovel node identity."""
        _require_instance(self.id, NodeId, "ShovelNode.id")
        _require_instance(self.source, ResourceEndpoint, "ShovelNode.source")
        _require_instance(self.destination, ResourceEndpoint, "ShovelNode.destination")
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
    _nodes: frozenset[TopologyNode] = field(init=False, repr=False, compare=False, hash=False)
    _all_node_ids: frozenset[NodeId] = field(init=False, repr=False, compare=False, hash=False)
    _nodes_by_id: Mapping[NodeId, TopologyNode] = field(init=False, repr=False, compare=False, hash=False)

    def __post_init__(self) -> None:
        """Validate graph consistency and construct immutable lookup views."""
        _require_optional_instance(self.cluster_id, str, "ClusterTopology.cluster_id")
        _require_optional_instance(self.cluster_name, str, "ClusterTopology.cluster_name")
        _require_optional_instance(self.cluster_label, str, "ClusterTopology.cluster_label")
        collections = (
            ("ClusterTopology.exchanges", self.exchanges, ExchangeNode),
            ("ClusterTopology.queues", self.queues, QueueNode),
            ("ClusterTopology.shovels", self.shovels, ShovelNode),
            ("ClusterTopology.edges", self.edges, TopologyEdge),
        )
        for field_name, values, value_type in collections:
            if not isinstance(values, frozenset):
                raise TopologyValidationError(f"{field_name} must be a frozenset, got {values!r}")
            for value in values:
                _require_instance(value, value_type, field_name)

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
        type["ClusterTopology"],
        tuple[
            frozenset[ExchangeNode],
            frozenset[QueueNode],
            frozenset[ShovelNode],
            frozenset[TopologyEdge],
            str | None,
            str | None,
            str | None,
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
