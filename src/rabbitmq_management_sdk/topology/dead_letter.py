"""Audit effective dead-letter configuration and captured routing evidence."""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from rabbitmq_management_sdk.exceptions import TopologyValidationError
from rabbitmq_management_sdk.topology.models import EdgeKind, NodeId, NodeKind, QueueNode
from rabbitmq_management_sdk.topology.ordering import edge_sort_key, node_sort_key

if TYPE_CHECKING:
    from rabbitmq_management_sdk.topology.models import ClusterTopology, TopologyEdge


class DeadLetterCoverageStatus(StrEnum):
    """Configuration outcomes, independent of operational severity."""

    NO_DLX = "no_dlx"
    MISSING_EXCHANGE = "missing_exchange"
    NO_DESTINATION = "no_destination"
    DESTINATION_POSSIBLE = "destination_possible"
    UNDETERMINED = "undetermined"
    NOT_APPLICABLE = "not_applicable"


class DeadLetterUncertainty(StrEnum):
    """Reasons that captured data cannot settle a routing decision."""

    ROUTING_KEY_NOT_CAPTURED = "routing_key_not_captured"
    MESSAGE_HEADERS_NOT_CAPTURED = "message_headers_not_captured"
    UNSUPPORTED_ROUTING_SEMANTICS = "unsupported_routing_semantics"
    MISSING_RESOURCE = "missing_resource"
    INCOMPLETE_BINDING = "incomplete_binding"
    UNSUPPORTED_QUEUE_TYPE = "unsupported_queue_type"
    CONFLICTING_ROUTES = "conflicting_routes"


@dataclass(frozen=True, slots=True)
class DeadLetterRoutingIssue:
    """One unresolved routing decision and the resource it concerns."""

    resource: NodeId
    reason: DeadLetterUncertainty

    def __post_init__(self) -> None:
        if not isinstance(self.resource, NodeId):
            raise TopologyValidationError("DeadLetterRoutingIssue.resource must be NodeId")
        if not isinstance(self.reason, DeadLetterUncertainty):
            raise TopologyValidationError("DeadLetterRoutingIssue.reason must be DeadLetterUncertainty")


@dataclass(frozen=True, slots=True)
class DeadLetterCoverage:
    """Effective DLX settings and routing evidence for one captured queue.

    ``destinations`` contains declared queues reached without relying on an
    unknown routing decision. ``destination_possible`` establishes at least
    one such route, not runtime delivery or completeness of the destination
    list. ``issues`` can accompany it when other branches remain unresolved.
    With no established destination, unresolved branches yield ``undetermined``.

    For a configured DLX, a ``None`` routing key means the original message
    keys are unknown; an empty string is a configured key. Terminal and intentional-discard queues
    are retained. Streams are explicitly ``not_applicable``.
    """

    queue: QueueNode
    status: DeadLetterCoverageStatus
    dead_letter_exchange: NodeId | None = None
    routing_key: str | None = None
    destinations: tuple[NodeId, ...] = ()
    issues: tuple[DeadLetterRoutingIssue, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.queue, QueueNode) or not isinstance(self.status, DeadLetterCoverageStatus):
            raise TopologyValidationError("DeadLetterCoverage requires a QueueNode and DeadLetterCoverageStatus")
        if self.routing_key is not None and not isinstance(self.routing_key, str):
            raise TopologyValidationError("DeadLetterCoverage.routing_key must be str or None")
        if self.dead_letter_exchange is not None and (
            not isinstance(self.dead_letter_exchange, NodeId) or self.dead_letter_exchange.kind != NodeKind.EXCHANGE
        ):
            raise TopologyValidationError("DeadLetterCoverage.dead_letter_exchange must be an exchange NodeId or None")
        if not isinstance(self.destinations, tuple) or any(
            not isinstance(node, NodeId) or node.kind != NodeKind.QUEUE for node in self.destinations
        ):
            raise TopologyValidationError("DeadLetterCoverage.destinations must be a tuple of queue NodeId values")
        if not isinstance(self.issues, tuple) or any(
            not isinstance(issue, DeadLetterRoutingIssue) for issue in self.issues
        ):
            raise TopologyValidationError("DeadLetterCoverage.issues must be a tuple of DeadLetterRoutingIssue values")
        references = (*self.destinations, *(issue.resource for issue in self.issues))
        if self.dead_letter_exchange is not None:
            references += (self.dead_letter_exchange,)
        if any((node.cluster_id, node.vhost) != (self.queue.id.cluster_id, self.queue.id.vhost) for node in references):
            raise TopologyValidationError("DeadLetterCoverage resources must share the queue cluster and vhost")
        self._validate_status()
        object.__setattr__(self, "destinations", tuple(sorted(set(self.destinations), key=node_sort_key)))
        object.__setattr__(
            self,
            "issues",
            tuple(sorted(set(self.issues), key=lambda issue: (node_sort_key(issue.resource), issue.reason.value))),
        )

    def _validate_status(self) -> None:
        if (self.status == DeadLetterCoverageStatus.DESTINATION_POSSIBLE) != bool(self.destinations):
            raise TopologyValidationError("DeadLetterCoverage destinations require destination_possible status")
        if (
            self.status
            in {
                DeadLetterCoverageStatus.MISSING_EXCHANGE,
                DeadLetterCoverageStatus.NO_DESTINATION,
                DeadLetterCoverageStatus.DESTINATION_POSSIBLE,
            }
            and self.dead_letter_exchange is None
        ):
            raise TopologyValidationError("DeadLetterCoverage status requires a configured exchange")
        if self.status == DeadLetterCoverageStatus.NO_DLX and (
            self.dead_letter_exchange is not None or self.routing_key is not None or self.issues
        ):
            raise TopologyValidationError("DeadLetterCoverage no_dlx cannot contain routing settings or issues")
        if self.status == DeadLetterCoverageStatus.UNDETERMINED and not self.issues:
            raise TopologyValidationError("DeadLetterCoverage undetermined requires routing issues")
        if (self.status == DeadLetterCoverageStatus.NOT_APPLICABLE) != (self.queue.queue_type == "stream"):
            raise TopologyValidationError("DeadLetterCoverage not_applicable is reserved for streams")


@dataclass(slots=True)
class _DeliveryEvidence:
    destinations: set[NodeId] = field(default_factory=set)
    conditional_destinations: set[NodeId] = field(default_factory=set)
    issues: set[DeadLetterRoutingIssue] = field(default_factory=set)
    open_branch: bool = False

    @property
    def status(self) -> DeadLetterCoverageStatus:
        if self.destinations:
            return DeadLetterCoverageStatus.DESTINATION_POSSIBLE
        if self.conditional_destinations or self.open_branch:
            return DeadLetterCoverageStatus.UNDETERMINED
        return DeadLetterCoverageStatus.NO_DESTINATION


class _DeadLetterRouter:
    """Index one immutable graph and evaluate individual DLX publications."""

    def __init__(self, topology: ClusterTopology) -> None:
        self.topology = topology
        self.bindings: dict[NodeId, list[TopologyEdge]] = {}
        self.alternates: dict[NodeId, list[TopologyEdge]] = {}
        self.dead_letters: dict[NodeId, list[TopologyEdge]] = {}
        for edge in sorted(topology.edges, key=edge_sort_key):
            if edge.kind == EdgeKind.BINDING:
                self.bindings.setdefault(edge.source, []).append(edge)
            elif edge.kind in {EdgeKind.ALTERNATE_EXCHANGE, EdgeKind.DEAD_LETTER}:
                routes = self.alternates if edge.kind == EdgeKind.ALTERNATE_EXCHANGE else self.dead_letters
                routes.setdefault(edge.source, []).append(edge)
        self.exchanges = {exchange.id: exchange for exchange in topology.exchanges}
        self.queues = {queue.id: queue for queue in topology.queues}
        self._evidence_cache: dict[tuple[NodeId, str | None], _DeliveryEvidence] = {}

    def coverage(self, queue: QueueNode) -> DeadLetterCoverage:
        edges = self.dead_letters.get(queue.id, ())
        edge = edges[0] if len(edges) == 1 else None
        exchange = edge.target if edge else None
        routing_key = edge.routing_key if edge else None
        if queue.queue_type == "stream":
            return DeadLetterCoverage(queue, DeadLetterCoverageStatus.NOT_APPLICABLE, exchange, routing_key)
        if queue.queue_type not in {"classic", "quorum"}:
            return DeadLetterCoverage(
                queue,
                DeadLetterCoverageStatus.UNDETERMINED,
                exchange,
                routing_key,
                issues=(DeadLetterRoutingIssue(queue.id, DeadLetterUncertainty.UNSUPPORTED_QUEUE_TYPE),),
            )
        if len(edges) > 1:
            return DeadLetterCoverage(
                queue,
                DeadLetterCoverageStatus.UNDETERMINED,
                issues=(DeadLetterRoutingIssue(queue.id, DeadLetterUncertainty.CONFLICTING_ROUTES),),
            )
        if exchange is None:
            return DeadLetterCoverage(queue, DeadLetterCoverageStatus.NO_DLX)
        if exchange not in self.exchanges:
            return DeadLetterCoverage(queue, DeadLetterCoverageStatus.MISSING_EXCHANGE, exchange, routing_key)
        cache_key = (exchange, routing_key)
        if cache_key not in self._evidence_cache:
            self._evidence_cache[cache_key] = self._trace(exchange, routing_key)
        evidence = self._evidence_cache[cache_key]
        return DeadLetterCoverage(
            queue, evidence.status, exchange, routing_key, tuple(evidence.destinations), tuple(evidence.issues)
        )

    def _trace(self, origin: NodeId, routing_key: str | None) -> _DeliveryEvidence:
        evidence = _DeliveryEvidence()
        pending = deque([(origin, True)])
        visited: set[tuple[NodeId, bool]] = set()
        while pending:
            node, established = pending.popleft()
            if (node, established) in visited:
                continue
            visited.add((node, established))
            if node not in self.topology.all_node_ids:
                evidence.issues.add(DeadLetterRoutingIssue(node, DeadLetterUncertainty.MISSING_RESOURCE))
                evidence.open_branch = True
            elif node.kind == NodeKind.QUEUE:
                targets = evidence.destinations if established else evidence.conditional_destinations
                targets.add(node)
            elif node.name == "":
                self._default_destinations(node, origin, routing_key, established, evidence)
            else:
                pending.extend(self._exchange_routes(node, routing_key, established, evidence))
        return evidence

    def _default_destinations(
        self, node: NodeId, origin: NodeId, routing_key: str | None, established: bool, evidence: _DeliveryEvidence
    ) -> None:
        if node != origin:
            evidence.issues.add(DeadLetterRoutingIssue(node, DeadLetterUncertainty.UNSUPPORTED_ROUTING_SEMANTICS))
            evidence.open_branch = True
            return
        queues = {queue for queue in self.queues if queue.vhost == node.vhost}
        if routing_key is None:
            if queues:
                evidence.issues.add(DeadLetterRoutingIssue(node, DeadLetterUncertainty.ROUTING_KEY_NOT_CAPTURED))
                evidence.conditional_destinations.update(queues)
        else:
            evidence.destinations.update(queue for queue in queues if queue.name == routing_key and established)

    def _exchange_routes(
        self, node: NodeId, routing_key: str | None, established: bool, evidence: _DeliveryEvidence
    ) -> list[tuple[NodeId, bool]]:
        exchange_type = self.exchanges[node].exchange_type
        if exchange_type not in {"direct", "topic", "fanout", "headers"}:
            evidence.issues.add(DeadLetterRoutingIssue(node, DeadLetterUncertainty.UNSUPPORTED_ROUTING_SEMANTICS))
            evidence.open_branch = True
            return [(alternate.target, False) for alternate in self.alternates.get(node, ())]
        matches = [(edge, *_binding_matches(exchange_type, edge, routing_key)) for edge in self.bindings.get(node, ())]
        routes = []
        for edge, matches_binding, reason in matches:
            if reason is not None:
                evidence.issues.add(DeadLetterRoutingIssue(node, reason))
            if matches_binding is not False:
                routes.append((edge.target, established and matches_binding is True))
        alternates = self.alternates.get(node, ())
        if alternates and not any(match is True for _, match, _ in matches):
            if len(alternates) > 1:
                evidence.issues.add(DeadLetterRoutingIssue(node, DeadLetterUncertainty.CONFLICTING_ROUTES))
                evidence.open_branch = True
            definite = established and len(alternates) == 1 and all(match is False for _, match, _ in matches)
            routes.extend((alternate.target, definite) for alternate in alternates)
        return routes


def _binding_matches(
    exchange_type: str, edge: TopologyEdge, routing_key: str | None
) -> tuple[bool | None, DeadLetterUncertainty | None]:
    if exchange_type == "fanout":
        return True, None
    if exchange_type == "headers":
        return _headers_match(edge.arguments)
    if edge.routing_key is None:
        return None, DeadLetterUncertainty.INCOMPLETE_BINDING
    if routing_key is None:
        universal = exchange_type == "topic" and edge.routing_key and set(edge.routing_key.split(".")) == {"#"}
        return (True, None) if universal else (None, DeadLetterUncertainty.ROUTING_KEY_NOT_CAPTURED)
    if exchange_type == "direct":
        return routing_key == edge.routing_key, None
    return _topic_matches(edge.routing_key, routing_key), None


def _headers_match(arguments: str | None) -> tuple[bool | None, DeadLetterUncertainty | None]:
    try:
        values = json.loads(arguments) if arguments is not None else {}
    except (ValueError, RecursionError):
        return None, DeadLetterUncertainty.INCOMPLETE_BINDING
    if not isinstance(values, dict):
        return None, DeadLetterUncertainty.INCOMPLETE_BINDING
    mode = values.get("x-match", "all")
    if mode not in ("all", "any", "all-with-x", "any-with-x"):
        return None, DeadLetterUncertainty.INCOMPLETE_BINDING
    keys = [key for key in values if key != "x-match" and (mode.endswith("with-x") or not key.startswith("x-"))]
    if not keys:
        return mode in ("all", "all-with-x"), None
    return None, DeadLetterUncertainty.MESSAGE_HEADERS_NOT_CAPTURED


def _topic_matches(pattern: str, routing_key: str) -> bool:
    """Match RabbitMQ topic words iteratively, with no regex approximation."""
    words = routing_key.split(".") if routing_key else []
    tokens = pattern.split(".") if pattern else []
    pending = [(0, 0)]
    seen = set()
    while pending:
        token, word = pending.pop()
        if (token, word) in seen:
            continue
        seen.add((token, word))
        if token == len(tokens):
            if word == len(words):
                return True
        elif tokens[token] == "#":
            pending.append((token + 1, word))
            if word < len(words):
                pending.append((token, word + 1))
        elif word < len(words) and tokens[token] in ("*", words[word]):
            pending.append((token + 1, word + 1))
    return False


def dead_letter_coverage(topology: ClusterTopology) -> tuple[DeadLetterCoverage, ...]:
    """Return a canonically ordered result for every contained queue.

    Effective arguments and selected regular-policy values are already resolved
    into dead-letter edges. Follow bindings and conditional alternate exchanges
    within the same routing operation; stop at queues. Ignore later dead-letter
    and shovel operations, message history, runtime health, and delivery safety.
    """
    router = _DeadLetterRouter(topology)
    return tuple(router.coverage(queue) for queue in sorted(topology.queues, key=lambda queue: node_sort_key(queue.id)))
