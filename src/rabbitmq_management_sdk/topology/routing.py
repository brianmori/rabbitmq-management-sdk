"""Shared exchange routing mechanics and immutable uncertainty values.

Consumers import the value types from the topology facade. Routing mechanics
are implementation details shared by exchange and dead-letter coverage.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from rabbitmq_management_sdk.exceptions import TopologyValidationError
from rabbitmq_management_sdk.topology.models import EdgeKind, NodeId, NodeKind
from rabbitmq_management_sdk.topology.ordering import edge_sort_key

if TYPE_CHECKING:
    from rabbitmq_management_sdk.topology.models import ClusterTopology, TopologyEdge


class RoutingStatus(StrEnum):
    """Destination evidence for the supplied routing-key context."""

    NO_DESTINATION = "no_destination"
    DESTINATION_POSSIBLE = "destination_possible"
    UNDETERMINED = "undetermined"


class RoutingUncertainty(StrEnum):
    """Reasons a captured exchange routing decision remains unresolved."""

    ROUTING_KEY_NOT_CAPTURED = "routing_key_not_captured"
    MESSAGE_HEADERS_NOT_CAPTURED = "message_headers_not_captured"
    UNSUPPORTED_ROUTING_SEMANTICS = "unsupported_routing_semantics"
    MISSING_RESOURCE = "missing_resource"
    INCOMPLETE_BINDING = "incomplete_binding"
    CONFLICTING_ROUTES = "conflicting_routes"
    POLICY_SELECTION_NOT_CAPTURED = "policy_selection_not_captured"
    EXCHANGE_ARGUMENTS_NOT_CAPTURED = "exchange_arguments_not_captured"


@dataclass(frozen=True, slots=True)
class RoutingIssue:
    """An unresolved routing decision and its complete resource identity."""

    resource: NodeId
    reason: RoutingUncertainty

    def __post_init__(self) -> None:
        if not isinstance(self.resource, NodeId):
            raise TopologyValidationError("RoutingIssue.resource must be NodeId")
        if not isinstance(self.reason, RoutingUncertainty):
            raise TopologyValidationError("RoutingIssue.reason must be RoutingUncertainty")


@dataclass(slots=True)
class _RoutingEvidence:
    destinations: set[NodeId] = field(default_factory=set)
    conditional_destinations: set[NodeId] = field(default_factory=set)
    issues: set[RoutingIssue] = field(default_factory=set)
    open_branch: bool = False

    @property
    def status(self) -> RoutingStatus:
        if self.destinations:
            return RoutingStatus.DESTINATION_POSSIBLE
        if self.conditional_destinations or self.open_branch:
            return RoutingStatus.UNDETERMINED
        return RoutingStatus.NO_DESTINATION


class _ExchangeRouter:
    """Index one immutable graph and evaluate exchange routing contexts."""

    def __init__(self, topology: ClusterTopology) -> None:
        self.topology = topology
        self.bindings: dict[NodeId, list[TopologyEdge]] = {}
        self.alternates: dict[NodeId, list[TopologyEdge]] = {}
        for edge in sorted(topology.edges, key=edge_sort_key):
            if edge.kind == EdgeKind.BINDING:
                self.bindings.setdefault(edge.source, []).append(edge)
            elif edge.kind == EdgeKind.ALTERNATE_EXCHANGE:
                self.alternates.setdefault(edge.source, []).append(edge)
        self.exchanges = {exchange.id: exchange for exchange in topology.exchanges}
        self.queues = {queue.id: queue for queue in topology.queues}
        self._evidence_cache: dict[tuple[NodeId, str | None], _RoutingEvidence] = {}

    def trace(self, origin: NodeId, routing_key: str | None) -> _RoutingEvidence:
        cache_key = (origin, routing_key)
        if cache_key not in self._evidence_cache:
            self._evidence_cache[cache_key] = self._trace(origin, routing_key)
        return self._evidence_cache[cache_key]

    def _trace(self, origin: NodeId, routing_key: str | None) -> _RoutingEvidence:
        evidence = _RoutingEvidence()
        pending = deque([(origin, True)])
        visited: set[tuple[NodeId, bool]] = set()
        while pending:
            node, established = pending.popleft()
            if (node, established) in visited:
                continue
            visited.add((node, established))
            if node not in self.topology.all_node_ids:
                evidence.issues.add(RoutingIssue(node, RoutingUncertainty.MISSING_RESOURCE))
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
        self, node: NodeId, origin: NodeId, routing_key: str | None, established: bool, evidence: _RoutingEvidence
    ) -> None:
        if node != origin:
            evidence.issues.add(RoutingIssue(node, RoutingUncertainty.UNSUPPORTED_ROUTING_SEMANTICS))
            evidence.open_branch = True
            return
        queues = {queue for queue in self.queues if queue.vhost == node.vhost}
        if routing_key is None:
            if queues:
                evidence.issues.add(RoutingIssue(node, RoutingUncertainty.ROUTING_KEY_NOT_CAPTURED))
                evidence.conditional_destinations.update(queues)
        else:
            evidence.destinations.update(queue for queue in queues if queue.name == routing_key and established)

    def _exchange_routes(
        self, node: NodeId, routing_key: str | None, established: bool, evidence: _RoutingEvidence
    ) -> list[tuple[NodeId, bool]]:
        exchange = self.exchanges[node]
        exchange_type = exchange.exchange_type
        if exchange_type not in {"direct", "topic", "fanout", "headers"}:
            evidence.issues.add(RoutingIssue(node, RoutingUncertainty.UNSUPPORTED_ROUTING_SEMANTICS))
            evidence.open_branch = True
            return [(alternate.target, False) for alternate in self.alternates.get(node, ())]
        matches = [(edge, *_binding_matches(exchange_type, edge, routing_key)) for edge in self.bindings.get(node, ())]
        routes = []
        for edge, matches_binding, reason in matches:
            if reason is not None:
                evidence.issues.add(RoutingIssue(node, reason))
            if matches_binding is not False:
                routes.append((edge.target, established and matches_binding is True))
        alternates = self.alternates.get(node, ())
        policy_unknown = exchange.alternate_exchange_policy_unknown
        arguments_unknown = exchange.alternate_exchange_arguments_unknown
        if (not alternates and not policy_unknown and not arguments_unknown) or any(
            match is True for _, match, _ in matches
        ):
            return routes
        if policy_unknown:
            evidence.issues.add(RoutingIssue(node, RoutingUncertainty.POLICY_SELECTION_NOT_CAPTURED))
            evidence.open_branch = True
        if arguments_unknown:
            evidence.issues.add(RoutingIssue(node, RoutingUncertainty.EXCHANGE_ARGUMENTS_NOT_CAPTURED))
            evidence.open_branch = True
        if alternates:
            if len(alternates) > 1:
                evidence.issues.add(RoutingIssue(node, RoutingUncertainty.CONFLICTING_ROUTES))
                evidence.open_branch = True
            definite = (
                established
                and not arguments_unknown
                and len(alternates) == 1
                and all(match is False for _, match, _ in matches)
            )
            routes.extend((alternate.target, definite) for alternate in alternates)
        return routes


def _binding_matches(
    exchange_type: str, edge: TopologyEdge, routing_key: str | None
) -> tuple[bool | None, RoutingUncertainty | None]:
    if exchange_type == "fanout":
        return True, None
    if exchange_type == "headers":
        return _headers_match(edge.arguments)
    if edge.routing_key is None:
        return None, RoutingUncertainty.INCOMPLETE_BINDING
    if routing_key is None:
        universal = exchange_type == "topic" and edge.routing_key and set(edge.routing_key.split(".")) == {"#"}
        return (True, None) if universal else (None, RoutingUncertainty.ROUTING_KEY_NOT_CAPTURED)
    if exchange_type == "direct":
        return routing_key == edge.routing_key, None
    return _topic_matches(edge.routing_key, routing_key), None


def _headers_match(arguments: str | None) -> tuple[bool | None, RoutingUncertainty | None]:
    try:
        values = json.loads(arguments) if arguments is not None else {}
    except (ValueError, RecursionError):
        return None, RoutingUncertainty.INCOMPLETE_BINDING
    if not isinstance(values, dict):
        return None, RoutingUncertainty.INCOMPLETE_BINDING
    mode = values.get("x-match", "all")
    if mode not in ("all", "any", "all-with-x", "any-with-x"):
        return None, RoutingUncertainty.INCOMPLETE_BINDING
    keys = [key for key in values if key != "x-match" and (mode.endswith("with-x") or not key.startswith("x-"))]
    if not keys:
        return mode in ("all", "all-with-x"), None
    return None, RoutingUncertainty.MESSAGE_HEADERS_NOT_CAPTURED


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
