"""Describe direct fanout destinations and captured quorum replication."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from rabbitmq_management_sdk.exceptions import TopologyValidationError
from rabbitmq_management_sdk.topology.models import EdgeKind, ExchangeNode, NodeId, NodeKind
from rabbitmq_management_sdk.topology.ordering import node_sort_key

if TYPE_CHECKING:
    from rabbitmq_management_sdk.topology.models import ClusterTopology


@dataclass(frozen=True, slots=True)
class FanoutQueue:
    """A distinct queue directly bound to a fanout exchange.

    Attributes:
        id: Complete queue identity, even when its declaration is missing.
        queue_type: Declared queue type, or ``None`` for a dangling target.
        replication_factor: Observed quorum membership count including the
            leader, or ``None`` when unknown or for other queue types. Initial
            group sizes and online-member counts are not replication factors.
    """

    id: NodeId
    queue_type: str | None = None
    replication_factor: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, NodeId) or self.id.kind != NodeKind.QUEUE:
            raise TopologyValidationError("FanoutQueue.id must be a queue NodeId")
        if self.queue_type is not None and not isinstance(self.queue_type, str):
            raise TopologyValidationError("FanoutQueue.queue_type must be str or None")
        if self.replication_factor is not None:
            if type(self.replication_factor) is not int or self.replication_factor < 1:
                raise TopologyValidationError("FanoutQueue.replication_factor must be a positive int or None")
            if self.queue_type != "quorum":
                raise TopologyValidationError("FanoutQueue.replication_factor requires a quorum queue")


@dataclass(frozen=True, slots=True)
class FanoutExchangeBindings:
    """One fanout exchange and its distinct, directly bound queue targets.

    Includes exchanges with no direct queue bindings. Queues are sorted by
    complete identity. This describes captured configuration, not a measured
    bottleneck, a disk-write estimate, or the complete set of destinations
    reachable through other exchanges, alternate exchanges, or shovels.
    """

    exchange: ExchangeNode
    queues: tuple[FanoutQueue, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.exchange, ExchangeNode) or self.exchange.exchange_type != "fanout":
            raise TopologyValidationError("FanoutExchangeBindings.exchange must be a fanout ExchangeNode")
        if not isinstance(self.queues, tuple) or any(not isinstance(queue, FanoutQueue) for queue in self.queues):
            raise TopologyValidationError("FanoutExchangeBindings.queues must be a tuple of FanoutQueue values")
        for queue in self.queues:
            if (queue.id.cluster_id, queue.id.vhost) != (self.exchange.id.cluster_id, self.exchange.id.vhost):
                raise TopologyValidationError("FanoutExchangeBindings queues must share the exchange cluster and vhost")
        if len({queue.id for queue in self.queues}) != len(self.queues):
            raise TopologyValidationError("FanoutExchangeBindings queues must have distinct identities")
        object.__setattr__(self, "queues", tuple(sorted(self.queues, key=lambda queue: node_sort_key(queue.id))))


def fanout_exchange_bindings(topology: ClusterTopology) -> tuple[FanoutExchangeBindings, ...]:
    """Return every contained fanout exchange and its direct queue bindings.

    Parallel bindings to the same queue contribute one target. Dangling queue
    targets retain their identities with unknown attributes; no queue node is
    invented. Only binding edges are counted, independently of routing keys.
    """
    exchanges = {exchange.id: exchange for exchange in topology.exchanges if exchange.exchange_type == "fanout"}
    targets: dict[NodeId, set[NodeId]] = {exchange_id: set() for exchange_id in exchanges}
    for edge in topology.edges:
        if edge.kind == EdgeKind.BINDING and edge.source in targets and edge.target.kind == NodeKind.QUEUE:
            targets[edge.source].add(edge.target)

    queues_by_id = {queue.id: queue for queue in topology.queues}
    results = []
    for exchange_id in sorted(exchanges, key=node_sort_key):
        queues = []
        for queue_id in sorted(targets[exchange_id], key=node_sort_key):
            node = queues_by_id.get(queue_id)
            queues.append(
                FanoutQueue(
                    id=queue_id,
                    queue_type=node.queue_type if node is not None else None,
                    replication_factor=node.replication_factor if node is not None else None,
                )
            )
        results.append(FanoutExchangeBindings(exchange=exchanges[exchange_id], queues=tuple(queues)))
    return tuple(results)
