"""Translate a RabbitMQ definitions export into a topology graph.

The parser converts a :class:`ClusterDefinitionsResponse` wire model from
``GET /api/definitions`` into the frozen-dataclass
:class:`ClusterTopology` domain model. Graph construction includes:

1. Exchange and queue nodes plus binding edges, including referenced
   broker-predeclared exchanges omitted by definitions exports.
2. Dead-letter and alternate-exchange edges from declared arguments.
3. Broker-observed user-policy resolution layered on top of declared
   arguments.
4. Shovel nodes plus up to two shovel edges per shovel.
"""

import json
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal

from rabbitmq_management_sdk.exceptions import TopologyError, TopologyParseError
from rabbitmq_management_sdk.resources.v4.admin.schemas.export_response import (
    ClusterDefinitionsResponse,
    DefinitionBinding,
    DefinitionExchange,
    DefinitionQueue,
)
from rabbitmq_management_sdk.resources.v4.exchanges.schemas.exchange_response import ExchangeResponse
from rabbitmq_management_sdk.topology.models import (
    ClusterTopology,
    EdgeKind,
    ExchangeNode,
    NodeId,
    NodeKind,
    QueueNode,
    ResourceEndpoint,
    ShovelNode,
    TopologyEdge,
)
from rabbitmq_management_sdk.topology.policy_routes import (
    UserPolicySelections,
    resolve_alternate_exchange,
    resolve_dead_letter_values,
)
from rabbitmq_management_sdk.topology.shovel import parse_shovel_endpoint

_EMPTY_USER_POLICY_SELECTIONS: UserPolicySelections = MappingProxyType({})
_PREDECLARED_EXCHANGE_TYPES: Mapping[str, str] = {
    "": "direct",
    "amq.direct": "direct",
    "amq.fanout": "fanout",
    "amq.headers": "headers",
    "amq.match": "headers",
    "amq.topic": "topic",
}


def _binding_arguments_repr(b: DefinitionBinding) -> str | None:
    """Serialize binding arguments for stable edge identity.

    Fields absent from the wire payload are omitted, while explicitly supplied
    ``null`` values are preserved. Keeping the complete supplied arguments
    distinguishes bindings that share a routing key but use different argument
    maps, which is the normal shape for headers exchanges.

    Args:
        b: Binding from the definitions export.

    Returns:
        Canonical, key-sorted JSON, or ``None`` when no arguments remain.
    """
    raw = b.arguments.model_dump(by_alias=True, exclude_unset=True)
    return json.dumps(raw, sort_keys=True) if raw else None


@dataclass(frozen=True, slots=True)
class _TopologyBuilder:
    """Build one cluster graph from normalized parser inputs."""

    response: ClusterDefinitionsResponse
    in_cluster_amqp_hosts: frozenset[str]
    cluster_label: str | None
    user_policy_selections: UserPolicySelections
    observed_exchanges: tuple[ExchangeResponse, ...]
    cluster_id: str | None = field(init=False)
    default_queue_types: Mapping[str, str | None] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """Derive values shared by multiple construction phases."""
        object.__setattr__(self, "cluster_id", self.response.internal_cluster_id)
        object.__setattr__(
            self,
            "default_queue_types",
            MappingProxyType(
                {
                    vhost.name: (vhost.metadata.default_queue_type if vhost.metadata else None)
                    for vhost in self.response.vhosts
                }
            ),
        )

    def build(self) -> ClusterTopology:
        """Run the graph-construction phases in dependency order."""
        exchanges = frozenset(self._exchange_node(exchange) for exchange in self.response.exchanges)
        queues = frozenset(self._queue_node(queue) for queue in self.response.queues)
        shovels, shovel_edges = self._shovels()

        edges = self._binding_edges() | self._dead_letter_edges() | self._alternate_exchange_edges() | shovel_edges
        exchanges |= self._referenced_exchange_supplements(exchanges, edges)
        cluster_name = self.response.cluster_name
        if cluster_name is None:
            cluster_name = self.response.original_cluster_name

        return ClusterTopology(
            exchanges=exchanges,
            queues=queues,
            shovels=shovels,
            edges=edges,
            cluster_id=self.cluster_id,
            cluster_name=cluster_name,
            cluster_label=self.cluster_label if self.cluster_label is not None else cluster_name,
        )

    def _node_id(self, *, vhost: str, name: str, kind: NodeKind) -> NodeId:
        """Build a resource identity scoped to this cluster."""
        return NodeId(cluster_id=self.cluster_id, vhost=vhost, name=name, kind=kind)

    def _exchange_node(self, exchange: DefinitionExchange | ExchangeResponse) -> ExchangeNode:
        """Translate a declared or observed exchange into a graph node."""
        return ExchangeNode(
            id=self._node_id(vhost=exchange.vhost, name=exchange.name, kind=NodeKind.EXCHANGE),
            exchange_type=exchange.type,
            internal=exchange.internal,
            durable=exchange.durable,
        )

    def _queue_node(self, queue: DefinitionQueue) -> QueueNode:
        """Translate a declared queue, applying its vhost default if needed."""
        queue_type = self._resolved_queue_type(queue)
        if queue_type is None:
            raise TopologyParseError(
                f"Queue {queue.vhost}/{queue.name} has no x-queue-type and vhost "
                f"{queue.vhost!r} has no default_queue_type; queue type cannot be determined"
            )
        return QueueNode(
            id=self._node_id(vhost=queue.vhost, name=queue.name, kind=NodeKind.QUEUE),
            queue_type=queue_type,
            durable=queue.durable,
        )

    def _resolved_queue_type(self, queue: DefinitionQueue) -> str | None:
        """Resolve a queue's explicit type or its virtual-host default."""
        return queue.arguments.queue_type or self.default_queue_types.get(queue.vhost)

    def _binding_edges(self) -> frozenset[TopologyEdge]:
        """Build binding edges from the definitions export."""
        edges = set()
        for binding in self.response.bindings:
            destination_kind = NodeKind.QUEUE if binding.destination_type == "queue" else NodeKind.EXCHANGE
            edges.add(
                TopologyEdge(
                    source=self._node_id(
                        vhost=binding.vhost,
                        name=binding.source,
                        kind=NodeKind.EXCHANGE,
                    ),
                    target=self._node_id(
                        vhost=binding.vhost,
                        name=binding.destination,
                        kind=destination_kind,
                    ),
                    kind=EdgeKind.BINDING,
                    routing_key=binding.routing_key,
                    arguments=_binding_arguments_repr(binding),
                )
            )
        return frozenset(edges)

    def _dead_letter_edges(self) -> frozenset[TopologyEdge]:
        """Build dead-letter edges from arguments and policy evidence."""
        edges = set()
        for queue in self.response.queues:
            source = self._node_id(vhost=queue.vhost, name=queue.name, kind=NodeKind.QUEUE)
            dlx, routing_key = resolve_dead_letter_values(
                queue_id=source,
                queue_type=self._resolved_queue_type(queue),
                declared_exchange=queue.arguments.dead_letter_exchange,
                declared_routing_key=queue.arguments.dead_letter_routing_key,
                policies=self.response.policies,
                user_policy_selections=self.user_policy_selections,
            )
            if dlx is not None:
                edges.add(
                    TopologyEdge(
                        source=source,
                        target=self._node_id(vhost=queue.vhost, name=dlx, kind=NodeKind.EXCHANGE),
                        kind=EdgeKind.DEAD_LETTER,
                        routing_key=routing_key,
                    )
                )
        return frozenset(edges)

    def _alternate_exchange_edges(self) -> frozenset[TopologyEdge]:
        """Build alternate-exchange edges from arguments and policy evidence."""
        edges = set()
        for exchange in self.response.exchanges:
            source = self._node_id(vhost=exchange.vhost, name=exchange.name, kind=NodeKind.EXCHANGE)
            alternate_exchange = resolve_alternate_exchange(
                exchange_id=source,
                declared_alternate_exchange=exchange.arguments.alternate_exchange,
                policies=self.response.policies,
                user_policy_selections=self.user_policy_selections,
            )
            if alternate_exchange is not None:
                edges.add(
                    TopologyEdge(
                        source=source,
                        target=self._node_id(
                            vhost=exchange.vhost,
                            name=alternate_exchange,
                            kind=NodeKind.EXCHANGE,
                        ),
                        kind=EdgeKind.ALTERNATE_EXCHANGE,
                    )
                )
        return frozenset(edges)

    def _shovel_endpoint(
        self,
        value: Mapping[str, object],
        side: Literal["src", "dest"],
        shovel_id: NodeId,
    ) -> tuple[ResourceEndpoint, TopologyEdge | None]:
        """Parse one shovel endpoint and build its edge when confirmed local."""
        endpoint = parse_shovel_endpoint(
            value,
            side,
            in_cluster_amqp_hosts=self.in_cluster_amqp_hosts,
        )
        if (
            endpoint.vhost is None
            or not endpoint.is_confirmed_local
            or endpoint.resource_name is None
            or endpoint.resource_kind is None
        ):
            return endpoint, None

        resource_id = self._node_id(
            vhost=endpoint.vhost,
            name=endpoint.resource_name,
            kind=endpoint.resource_kind,
        )
        source, target = (resource_id, shovel_id) if side == "src" else (shovel_id, resource_id)
        return (
            endpoint,
            TopologyEdge(
                source=source,
                target=target,
                kind=EdgeKind.SHOVEL,
                routing_key=endpoint.routing_key,
            ),
        )

    def _shovels(self) -> tuple[frozenset[ShovelNode], frozenset[TopologyEdge]]:
        """Build shovel nodes and edges for their confirmed local endpoints."""
        shovels: set[ShovelNode] = set()
        edges: set[TopologyEdge] = set()

        for parameter in self.response.parameters:
            if parameter.component != "shovel":
                continue

            shovel_id = self._node_id(
                vhost=parameter.vhost,
                name=parameter.name,
                kind=NodeKind.SHOVEL,
            )
            source, source_edge = self._shovel_endpoint(parameter.value, "src", shovel_id)
            destination, destination_edge = self._shovel_endpoint(parameter.value, "dest", shovel_id)
            shovels.add(ShovelNode(id=shovel_id, source=source, destination=destination))
            edges.update(edge for edge in (source_edge, destination_edge) if edge is not None)

        return frozenset(shovels), frozenset(edges)

    def _referenced_exchange_supplements(
        self,
        declared_exchanges: frozenset[ExchangeNode],
        edges: frozenset[TopologyEdge],
    ) -> frozenset[ExchangeNode]:
        """Supply referenced exchanges omitted by definitions exports."""
        declared_vhosts = frozenset(vhost.name for vhost in self.response.vhosts)
        declared_ids = frozenset(exchange.id for exchange in declared_exchanges)
        referenced_ids = frozenset(
            node
            for edge in edges
            for node in (edge.source, edge.target)
            if node.kind == NodeKind.EXCHANGE and node.vhost in declared_vhosts
        )
        supplement_ids = referenced_ids - declared_ids

        observed_nodes = frozenset(
            node for exchange in self.observed_exchanges if (node := self._exchange_node(exchange)).id in supplement_ids
        )
        known_ids = declared_ids | {node.id for node in observed_nodes}
        synthesized_nodes = frozenset(
            ExchangeNode(
                id=node_id,
                exchange_type=_PREDECLARED_EXCHANGE_TYPES[node_id.name],
                internal=False,
                durable=True,
            )
            for node_id in referenced_ids - known_ids
            if node_id.name in _PREDECLARED_EXCHANGE_TYPES
        )
        return observed_nodes | synthesized_nodes


def parse_cluster_topology(
    response: ClusterDefinitionsResponse,
    *,
    in_cluster_amqp_hosts: Collection[str] = (),
    cluster_label: str | None = None,
    user_policy_selections: UserPolicySelections | None = None,
    observed_exchanges: Collection[ExchangeResponse] = (),
) -> ClusterTopology:
    """Build a graph for the cluster represented by ``response``.

    ``in_cluster_amqp_hosts`` supplies membership evidence for hosted AMQP
    shovel endpoints. ``local`` protocol shovels and hostless AMQP URIs do not
    need that evidence. An endpoint that is unresolved or not confirmed local
    remains visible on its :class:`ShovelNode`, but its resource edge is
    excluded so it cannot be merged with a same-named local resource.

    When present, the export's broker-generated ``internal_cluster_id`` is the
    graph and node identity. Older or manually constructed exports without that
    parameter retain ``None``; their nodes must not be used to merge
    independently exported clusters. The broker's ``cluster_name`` is retained
    separately as composition evidence, falling back to
    ``original_cluster_name`` in older export shapes. ``cluster_label`` is
    optional display metadata; when omitted, it defaults to that retained
    broker name.
    Neither label changes node identity.

    ``user_policy_selections`` is normalized broker evidence for
    policy-derived routes. It is independent of how queue and exchange
    observations were acquired. The parser does not evaluate RabbitMQ policy
    regular expressions locally; where direct arguments do not settle a route,
    a routing-relevant policy requires a selection record.

    ``observed_exchanges`` supplies Management API observations for
    broker-created exchanges omitted by the definitions export and referenced
    by parsed routes. Definitions remain authoritative for declared exchanges.
    A guaranteed standard exchange is synthesized when a referenced exchange
    is neither declared nor observed.

    Args:
        response: Validated cluster-wide definitions export.
        in_cluster_amqp_hosts: AMQP URI hosts confirmed to resolve to the
            represented cluster. Matching is case-insensitive.
        cluster_label: Optional human-readable label for the resulting graph.
        user_policy_selections: Broker-observed regular-policy selections by
            resource.
        observed_exchanges: Management API exchange observations used to
            supplement referenced exchanges.

    Returns:
        The parsed, immutable cluster topology.

    Raises:
        TopologyError: If the definitions and supplied observations cannot be
            translated into a consistent topology.
    """
    try:
        return _TopologyBuilder(
            response,
            in_cluster_amqp_hosts=frozenset(host.casefold() for host in in_cluster_amqp_hosts),
            cluster_label=cluster_label,
            user_policy_selections=(
                user_policy_selections if user_policy_selections is not None else _EMPTY_USER_POLICY_SELECTIONS
            ),
            observed_exchanges=tuple(observed_exchanges),
        ).build()
    except TopologyError:
        raise
    except (AttributeError, TypeError, ValueError) as exc:
        raise TopologyParseError("Could not translate definitions into a topology graph") from exc
