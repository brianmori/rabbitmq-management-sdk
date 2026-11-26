"""Translate validated RabbitMQ configuration into a topology graph.

The parser converts a :class:`ClusterDefinitionsResponse` wire model from
``GET /api/definitions`` plus optional broker observations into the immutable
:class:`ClusterTopology` domain model. Its edges are configured possible hops,
not guarantees that a particular message will traverse them.

Graph construction includes declared queues, exchanges, and bindings;
dead-letter and alternate-exchange settings resolved from direct arguments and
selected regular policies; shovel endpoint evidence; and referenced exchanges
omitted by definitions exports.
"""

import json
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Literal

from rabbitmq_management_sdk.exceptions import TopologyError, TopologyParseError, TopologyValidationError
from rabbitmq_management_sdk.resources.v4.admin.schemas.export_response import (
    ClusterDefinitionsResponse,
    ClusterExportBinding,
    ClusterExportExchange,
    ClusterExportQueue,
)
from rabbitmq_management_sdk.resources.v4.exchanges.schemas.exchange_response import ExchangeResponse
from rabbitmq_management_sdk.resources.v4.queues.schemas.queue_response import (
    ClassicQueueResponse,
    QueueResponse,
    QuorumQueueResponse,
)
from rabbitmq_management_sdk.topology.models import (
    ClusterTopology,
    EdgeKind,
    ExchangeNode,
    NodeId,
    NodeKind,
    QueueNode,
    QuorumPlacement,
    ResourceEndpoint,
    ShovelNode,
    TopologyEdge,
    VirtualHostMetadata,
)
from rabbitmq_management_sdk.topology.policy_inventory import capture_policy_inventory
from rabbitmq_management_sdk.topology.policy_routes import (
    UserPolicySelections,
    _apply_to_matches,
    _policies_that_can_set,
    resolve_alternate_exchange,
    resolve_dead_letter_values,
)
from rabbitmq_management_sdk.topology.queue_configuration import _Resolver, captured_broker_version
from rabbitmq_management_sdk.topology.queue_limits import resolve_queue_limits
from rabbitmq_management_sdk.topology.quorum_configuration import resolve_quorum_delivery
from rabbitmq_management_sdk.topology.shovel import (
    confirmed_local_resource_id,
    parse_shovel_acknowledgement,
    parse_shovel_endpoint,
)
from rabbitmq_management_sdk.topology.vhost_permissions import capture_vhost_permissions

_EMPTY_USER_POLICY_SELECTIONS: UserPolicySelections = MappingProxyType({})
_PREDECLARED_EXCHANGE_TYPES: Mapping[str, str] = {
    "": "direct",
    "amq.direct": "direct",
    "amq.fanout": "fanout",
    "amq.headers": "headers",
    "amq.match": "headers",
    "amq.topic": "topic",
}


def _binding_arguments_repr(b: ClusterExportBinding) -> str | None:
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
    observed_queues: tuple[QueueResponse, ...]
    cluster_id: str | None = field(init=False)
    default_queue_types: Mapping[str, str | None] = field(init=False, repr=False)
    queues_by_id: Mapping[NodeId, QueueResponse] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """Derive values shared by multiple construction phases."""
        object.__setattr__(self, "cluster_id", self.response.internal_cluster_id)
        queues_by_id: dict[NodeId, QueueResponse] = {}
        for queue in self.observed_queues:
            queue_id = self._node_id(vhost=queue.vhost, name=queue.name, kind=NodeKind.QUEUE)
            if queue_id in queues_by_id:
                raise TopologyParseError(f"Duplicate queue observations for {queue_id}")
            queues_by_id[queue_id] = queue
        object.__setattr__(self, "queues_by_id", MappingProxyType(queues_by_id))
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
        queue_routes = tuple(self._queue_and_dead_letter_edge(queue) for queue in self.response.queues)
        queues = frozenset(queue for queue, _ in queue_routes)
        dead_letter_edges = frozenset(edge for _, edge in queue_routes if edge is not None)
        shovels, shovel_edges = self._shovels()

        edges = self._binding_edges() | dead_letter_edges | shovel_edges
        alternate_edges, missing_exchange_arguments = self._alternate_exchange_routes()
        edges |= alternate_edges
        exchanges |= self._referenced_exchange_supplements(exchanges, edges)
        exchanges = self._with_alternate_evidence(exchanges, alternate_edges, missing_exchange_arguments)
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
            vhosts=frozenset(
                VirtualHostMetadata(
                    name=vhost.name,
                    default_queue_type=vhost.metadata.default_queue_type if vhost.metadata else None,
                )
                for vhost in self.response.vhosts
            ),
            vhost_permissions=capture_vhost_permissions(
                self.response,
                resource_vhosts={node.id.vhost for nodes in (exchanges, queues, shovels) for node in nodes},
            ),
            policy_inventory=capture_policy_inventory(
                self.response,
                cluster_id=self.cluster_id,
                queues=queues,
                exchanges=exchanges,
                observed_queues=self.observed_queues,
                observed_exchanges=self.observed_exchanges,
            ),
        )

    def _node_id(self, *, vhost: str, name: str, kind: NodeKind) -> NodeId:
        """Build a resource identity scoped to this cluster."""
        return NodeId(cluster_id=self.cluster_id, vhost=vhost, name=name, kind=kind)

    def _exchange_node(self, exchange: ClusterExportExchange | ExchangeResponse) -> ExchangeNode:
        """Translate a declared or observed exchange into a graph node."""
        return ExchangeNode(
            id=self._node_id(vhost=exchange.vhost, name=exchange.name, kind=NodeKind.EXCHANGE),
            exchange_type=exchange.type,
            internal=exchange.internal,
            durable=exchange.durable,
        )

    def _queue_and_dead_letter_edge(self, queue: ClusterExportQueue) -> tuple[QueueNode, TopologyEdge | None]:
        """Translate a queue declaration and its dead-letter route evidence."""
        queue_type = self._resolved_queue_type(queue)
        if queue_type is None:
            raise TopologyParseError(
                f"Queue {queue.vhost}/{queue.name} has no x-queue-type and vhost "
                f"{queue.vhost!r} has no default_queue_type; queue type cannot be determined"
            )
        queue_id = self._node_id(vhost=queue.vhost, name=queue.name, kind=NodeKind.QUEUE)
        observed = self.queues_by_id.get(queue_id)
        if observed is not None and observed.type != queue_type:
            raise TopologyParseError(f"Queue {queue_id} has inconsistent declared and observed queue types")
        resolver = _Resolver(
            self.response,
            queue,
            observed,
            selected_policy=self.user_policy_selections.get(queue_id),
            selection_captured=queue_id in self.user_policy_selections,
            queue_type=queue_type,
        )
        quorum_delivery = resolve_quorum_delivery(resolver) if queue_type == "quorum" else None
        dlx, routing_key, route_unknown = self._dead_letter_route(queue, queue_id, queue_type, observed)
        node = QueueNode(
            id=queue_id,
            queue_type=queue_type,
            durable=queue.durable,
            quorum_placement=self._quorum_placement(queue_id, observed) if queue_type == "quorum" else None,
            quorum_delivery=quorum_delivery,
            queue_limits=resolve_queue_limits(resolver, quorum_delivery),
            exclusive=observed.exclusive if observed is not None and observed.durable == queue.durable else None,
            dead_letter_route_unknown=route_unknown,
            unresolved_dead_letter_exchange=dlx if route_unknown else None,
        )
        if dlx is None or route_unknown:
            return node, None
        return node, TopologyEdge(
            source=queue_id,
            target=self._node_id(vhost=queue.vhost, name=dlx, kind=NodeKind.EXCHANGE),
            kind=EdgeKind.DEAD_LETTER,
            routing_key=routing_key,
        )

    @staticmethod
    def _quorum_placement(queue_id: NodeId, observed: QueueResponse | None) -> QuorumPlacement | None:
        """Copy placement fields without treating the observation's node as leader."""
        if observed is None:
            return None
        try:
            return QuorumPlacement(
                members=tuple(observed.members) if observed.members is not None else None,
                leader=observed.leader or None,
                online_members=tuple(observed.online) if observed.online is not None else None,
            )
        except TopologyValidationError as exc:
            raise TopologyParseError(f"Queue {queue_id} has invalid quorum membership evidence: {exc}") from exc

    def _resolved_queue_type(self, queue: ClusterExportQueue) -> str | None:
        """Resolve a queue's explicit type or its virtual-host default."""
        return queue.arguments.queue_type or self.default_queue_types.get(queue.vhost)

    def _binding_edges(self) -> frozenset[TopologyEdge]:
        """Build configured binding hops from the definitions export."""
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

    def _dead_letter_route(
        self, queue: ClusterExportQueue, queue_id: NodeId, queue_type: str, observed: QueueResponse | None
    ) -> tuple[str | None, str | None, bool]:
        """Resolve exchange and key, retaining a captured name if the route is unknown."""
        arguments_captured = "arguments" in queue.model_fields_set
        direct_exchange = queue.arguments.dead_letter_exchange
        direct_key = queue.arguments.dead_letter_routing_key
        if not arguments_captured and observed is not None:
            observed_arguments = observed.arguments
            if isinstance(observed_arguments, (ClassicQueueResponse, QuorumQueueResponse)) and (
                observed_arguments.queue_type == queue_type
            ):
                arguments_captured = True
                direct_exchange = observed_arguments.dead_letter_exchange
                direct_key = observed_arguments.dead_letter_routing_key

        dlx, routing_key = resolve_dead_letter_values(
            queue_id=queue_id,
            queue_type=queue_type,
            declared_exchange=direct_exchange,
            declared_routing_key=direct_key,
            policies=self.response.policies,
            user_policy_selections=self.user_policy_selections,
        )
        if queue_type not in {"classic", "quorum"}:
            return dlx, routing_key, False
        if not arguments_captured:
            return None, None, True
        if dlx is not None or self.user_policy_selections.get(queue_id) is not None:
            return dlx, routing_key, False

        # The policy adapter maps omitted observations to None for graph
        # construction. Absence requires a captured selector or policy evidence.
        selection_captured = (
            "policy" in observed.model_fields_set if observed is not None else queue_id in self.user_policy_selections
        )
        if selection_captured:
            return None, routing_key, False
        if observed is not None and (effective := observed.effective_policy_definition) is not None:
            return effective.dead_letter_exchange, None, "dead-letter-exchange" in effective.root
        policies_unknown = "policies" not in self.response.model_fields_set or bool(
            _policies_that_can_set(
                vhost=queue_id.vhost,
                kind=NodeKind.QUEUE,
                queue_type=queue_type,
                policies=self.response.policies,
                definition_field="dead_letter_exchange",
            )
        )
        return None, routing_key, policies_unknown

    def _alternate_exchange_routes(self) -> tuple[frozenset[TopologyEdge], frozenset[NodeId]]:
        """Build captured alternate hops and record exchanges with no argument map."""
        declared_ids = frozenset(
            self._node_id(vhost=exchange.vhost, name=exchange.name, kind=NodeKind.EXCHANGE)
            for exchange in self.response.exchanges
        )
        observed_by_id = {
            self._node_id(vhost=exchange.vhost, name=exchange.name, kind=NodeKind.EXCHANGE): exchange
            for exchange in self.observed_exchanges
        }
        exchange_sources: tuple[ClusterExportExchange | ExchangeResponse, ...] = (
            *self.response.exchanges,
            *(exchange for exchange_id, exchange in observed_by_id.items() if exchange_id not in declared_ids),
        )

        edges = set()
        missing_arguments = set()
        for exchange in exchange_sources:
            source = self._node_id(vhost=exchange.vhost, name=exchange.name, kind=NodeKind.EXCHANGE)
            argument_source = exchange if "arguments" in exchange.model_fields_set else observed_by_id.get(source)
            direct_alternate = None
            if argument_source is None or "arguments" not in argument_source.model_fields_set:
                missing_arguments.add(source)
            else:
                direct_alternate = argument_source.arguments.alternate_exchange
            alternate_exchange = resolve_alternate_exchange(
                exchange_id=source,
                declared_alternate_exchange=direct_alternate,
                policies=self.response.policies,
                user_policy_selections=self.user_policy_selections,
            )
            if alternate_exchange is not None and source not in missing_arguments:
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
        return frozenset(edges), frozenset(missing_arguments)

    def _with_alternate_evidence(
        self,
        exchanges: frozenset[ExchangeNode],
        alternate_edges: frozenset[TopologyEdge],
        missing_arguments: frozenset[NodeId],
    ) -> frozenset[ExchangeNode]:
        """Retain unavailable alternate routing evidence without adding route edges."""
        policies_captured = "policies" in self.response.model_fields_set
        alternate_policy_vhosts = {
            policy.vhost
            for policy in self.response.policies
            if policy.definition.alternate_exchange is not None
            and _apply_to_matches(policy.apply_to, NodeKind.EXCHANGE, None)
        }
        if policies_captured and not alternate_policy_vhosts and not missing_arguments:
            return exchanges

        # The broker can omit the policy field on unselected exchanges. Keep
        # that omission distinct from explicit null when evaluating routes.
        omitted_selections = {
            self._node_id(vhost=exchange.vhost, name=exchange.name, kind=NodeKind.EXCHANGE)
            for exchange in self.observed_exchanges
            if "policy" not in exchange.model_fields_set
        }
        resolved = {edge.source for edge in alternate_edges}
        resolved.update(
            resource
            for resource, name in self.user_policy_selections.items()
            if resource.kind == NodeKind.EXCHANGE and (name is not None or resource not in omitted_selections)
        )
        missing_policy = {
            exchange.id
            for exchange in exchanges
            if exchange.id not in resolved and (not policies_captured or exchange.id.vhost in alternate_policy_vhosts)
        }
        uncertain = missing_policy | missing_arguments
        return frozenset(
            replace(
                exchange,
                alternate_exchange_policy_unknown=exchange.id in missing_policy,
                alternate_exchange_arguments_unknown=exchange.id in missing_arguments,
            )
            if not exchange.is_default and exchange.id in uncertain
            else exchange
            for exchange in exchanges
        )

    def _shovel_endpoint(
        self,
        value: Mapping[str, object],
        side: Literal["src", "dest"],
        shovel_id: NodeId,
        *,
        supported_broker: bool,
    ) -> tuple[ResourceEndpoint, TopologyEdge | None]:
        """Parse one shovel side and emit its edge when local and fixed."""
        endpoint = parse_shovel_endpoint(
            value,
            side,
            in_cluster_amqp_hosts=self.in_cluster_amqp_hosts,
            supported_broker=supported_broker,
        )
        resource_id = confirmed_local_resource_id(endpoint, cluster_id=shovel_id.cluster_id)
        if resource_id is None:
            return endpoint, None

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
        """Build every shovel node and its fixed, confirmed-local endpoint edges."""
        shovels: set[ShovelNode] = set()
        edges: set[TopologyEdge] = set()
        _, minor = captured_broker_version(self.response)
        supported_broker = minor is not None

        for parameter in self.response.parameters:
            if parameter.component != "shovel":
                continue
            if not isinstance(parameter.value, dict):
                raise TopologyParseError(
                    f"Shovel {parameter.name!r} in vhost {parameter.vhost!r} requires a JSON object value"
                )

            shovel_id = self._node_id(
                vhost=parameter.vhost,
                name=parameter.name,
                kind=NodeKind.SHOVEL,
            )
            source, source_edge = self._shovel_endpoint(
                parameter.value, "src", shovel_id, supported_broker=supported_broker
            )
            destination, destination_edge = self._shovel_endpoint(
                parameter.value, "dest", shovel_id, supported_broker=supported_broker
            )
            acknowledgement = parse_shovel_acknowledgement(
                parameter.value, source.protocol, destination.protocol, supported_broker=supported_broker
            )
            shovels.add(ShovelNode(shovel_id, source, destination, acknowledgement))
            edges.update(edge for edge in (source_edge, destination_edge) if edge is not None)

        return frozenset(shovels), frozenset(edges)

    def _referenced_exchange_supplements(
        self,
        declared_exchanges: frozenset[ExchangeNode],
        edges: frozenset[TopologyEdge],
    ) -> frozenset[ExchangeNode]:
        """Supply observed or standard predeclared exchanges omitted by definitions."""
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
    observed_queues: Collection[QueueResponse] = (),
) -> ClusterTopology:
    """Build a configuration graph for the cluster represented by ``response``.

    Each resulting edge is a configured possible message hop. The graph does
    not evaluate message routing keys, headers, dead-letter triggers, shovel
    runtime state, or later configuration changes.

    ``in_cluster_amqp_hosts`` supplies membership evidence for hosted AMQP
    shovel endpoints. Valid direct AMQP URIs and ``local`` protocol endpoints
    supply locality evidence on their own. Each :class:`ShovelNode` retains
    unresolved endpoint evidence. A resource edge requires a fixed resource
    identity and confirmed locality.

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
    policy-derived routing values. It is independent of how queue and exchange
    observations were acquired. The parser does not evaluate RabbitMQ policy
    regular expressions locally; where direct arguments do not settle a
    routing value, a relevant policy requires a selection record.

    ``observed_exchanges`` supplies Management API observations for
    broker-created exchanges omitted by the definitions export and referenced
    by parsed hops. An observation can also supply a declared exchange's
    alternate argument when its definition omits the argument map.
    For a referenced exchange in a declared vhost, a standard predeclared
    RabbitMQ exchange is synthesized when it is neither declared nor observed.

    Args:
        response: Validated cluster-wide definitions export.
        in_cluster_amqp_hosts: AMQP URI hosts confirmed to resolve to the
            represented cluster. Matching is case-insensitive.
        cluster_label: Optional human-readable label for the resulting graph.
        user_policy_selections: Broker-observed regular-policy selections by
            resource.
        observed_exchanges: Management API exchange observations used to
            supplement referenced exchanges and recover omitted argument maps.
        observed_queues: Management API queue observations used to retain
            quorum members, leader, online members, and delivery evidence on declared queues.
            Missing or empty membership means unknown; initial group sizes
            are not used. Selected/effective policy evidence resolves delivery
            settings; missing global timeout defaults remain unknown.

    Returns:
        The parsed immutable configuration graph.

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
            observed_queues=tuple(observed_queues),
        ).build()
    except TopologyError:
        raise
    except (AttributeError, TypeError, ValueError) as exc:
        raise TopologyParseError("Could not translate definitions into a topology graph") from exc
