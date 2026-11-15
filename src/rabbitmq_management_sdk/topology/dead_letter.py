"""Audit effective dead-letter configuration and captured routing evidence."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from rabbitmq_management_sdk.exceptions import TopologyValidationError
from rabbitmq_management_sdk.topology.models import EdgeKind, NodeId, NodeKind, QueueNode
from rabbitmq_management_sdk.topology.ordering import node_sort_key
from rabbitmq_management_sdk.topology.routing import _ExchangeRouter

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


class _DeadLetterRouter:
    """Resolve queue-specific settings and delegate exchange traversal."""

    def __init__(self, topology: ClusterTopology) -> None:
        self.router = _ExchangeRouter(topology)
        self.dead_letters: dict[NodeId, list[TopologyEdge]] = {}
        for edge in topology.edges:
            if edge.kind == EdgeKind.DEAD_LETTER:
                self.dead_letters.setdefault(edge.source, []).append(edge)

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
        if exchange not in self.router.exchanges:
            return DeadLetterCoverage(queue, DeadLetterCoverageStatus.MISSING_EXCHANGE, exchange, routing_key)
        evidence = self.router.trace(exchange, routing_key)
        issues = tuple(
            DeadLetterRoutingIssue(issue.resource, DeadLetterUncertainty(issue.reason.value))
            for issue in evidence.issues
        )
        return DeadLetterCoverage(
            queue,
            DeadLetterCoverageStatus(evidence.status.value),
            exchange,
            routing_key,
            tuple(evidence.destinations),
            issues,
        )


def dead_letter_coverage(topology: ClusterTopology) -> tuple[DeadLetterCoverage, ...]:
    """Return a canonically ordered result for every contained queue.

    Effective arguments and selected regular-policy values are already resolved
    into dead-letter edges. Follow bindings and conditional alternate exchanges
    within the same routing operation; stop at queues. Ignore later dead-letter
    and shovel operations, message history, runtime health, and delivery safety.
    """
    router = _DeadLetterRouter(topology)
    return tuple(router.coverage(queue) for queue in sorted(topology.queues, key=lambda queue: node_sort_key(queue.id)))
