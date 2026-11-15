"""Describe routing evidence for every contained exchange and one key context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from rabbitmq_management_sdk.exceptions import TopologyAnalysisError, TopologyValidationError
from rabbitmq_management_sdk.topology.models import ExchangeNode, NodeId, NodeKind
from rabbitmq_management_sdk.topology.ordering import node_sort_key
from rabbitmq_management_sdk.topology.routing import RoutingIssue, RoutingStatus, _ExchangeRouter

if TYPE_CHECKING:
    from rabbitmq_management_sdk.topology.models import ClusterTopology


@dataclass(frozen=True, slots=True)
class ExchangeRoutingCoverage:
    """Captured destination evidence for an exchange and optional routing key.

    ``routing_key=None`` leaves key-dependent decisions unresolved; ``""`` is
    an explicit empty key. ``no_destination`` applies to this context, rather
    than asserting that every possible publication is unroutable. Destination
    existence never guarantees runtime acceptance or delivery.

    Destinations contain only declared queues reached through known decisions.
    Issues can coexist with a possible destination when other branches remain
    unresolved. Discard intent and operational severity belong to the caller.
    """

    exchange: ExchangeNode
    status: RoutingStatus
    routing_key: str | None = None
    destinations: tuple[NodeId, ...] = ()
    issues: tuple[RoutingIssue, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.exchange, ExchangeNode) or not isinstance(self.status, RoutingStatus):
            raise TopologyValidationError("ExchangeRoutingCoverage requires an ExchangeNode and RoutingStatus")
        if self.routing_key is not None and not isinstance(self.routing_key, str):
            raise TopologyValidationError("ExchangeRoutingCoverage.routing_key must be str or None")
        if not isinstance(self.destinations, tuple) or any(
            not isinstance(node, NodeId) or node.kind != NodeKind.QUEUE for node in self.destinations
        ):
            raise TopologyValidationError("ExchangeRoutingCoverage.destinations must be a tuple of queue NodeId values")
        if not isinstance(self.issues, tuple) or any(not isinstance(issue, RoutingIssue) for issue in self.issues):
            raise TopologyValidationError("ExchangeRoutingCoverage.issues must be a tuple of RoutingIssue values")
        references = (*self.destinations, *(issue.resource for issue in self.issues))
        if any(
            (node.cluster_id, node.vhost) != (self.exchange.id.cluster_id, self.exchange.id.vhost)
            for node in references
        ):
            raise TopologyValidationError("ExchangeRoutingCoverage resources must share the exchange cluster and vhost")
        if (self.status == RoutingStatus.DESTINATION_POSSIBLE) != bool(self.destinations):
            raise TopologyValidationError("ExchangeRoutingCoverage destinations require destination_possible status")
        if self.status == RoutingStatus.UNDETERMINED and not self.issues:
            raise TopologyValidationError("ExchangeRoutingCoverage undetermined requires routing issues")
        object.__setattr__(self, "destinations", tuple(sorted(set(self.destinations), key=node_sort_key)))
        object.__setattr__(
            self,
            "issues",
            tuple(sorted(set(self.issues), key=lambda issue: (node_sort_key(issue.resource), issue.reason.value))),
        )


def exchange_routing_coverage(
    topology: ClusterTopology, *, routing_key: str | None = None
) -> tuple[ExchangeRoutingCoverage, ...]:
    """Evaluate all contained exchanges for a shared optional routing key.

    Follow bindings and conditional alternate exchanges, including the default
    exchange's implicit routes when it is the origin. Stop at queues; shovels
    and dead-lettering begin later operations. Message headers are not supplied.
    """
    if routing_key is not None and not isinstance(routing_key, str):
        raise TopologyAnalysisError("routing_key must be str or None")
    router = _ExchangeRouter(topology)
    results = []
    for exchange in sorted(topology.exchanges, key=lambda exchange: node_sort_key(exchange.id)):
        evidence = router.trace(exchange.id, routing_key)
        results.append(
            ExchangeRoutingCoverage(
                exchange=exchange,
                status=evidence.status,
                routing_key=routing_key,
                destinations=tuple(evidence.destinations),
                issues=tuple(evidence.issues),
            )
        )
    return tuple(results)
