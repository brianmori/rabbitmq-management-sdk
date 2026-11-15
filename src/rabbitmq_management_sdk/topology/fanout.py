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
        durable: Captured queue durability, or ``None`` for a dangling target
            or a manually constructed value without durability evidence. This
            is a queue declaration property, not message persistence evidence.
    """

    id: NodeId
    queue_type: str | None = None
    replication_factor: int | None = None
    durable: bool | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, NodeId) or self.id.kind != NodeKind.QUEUE:
            raise TopologyValidationError("FanoutQueue.id must be a queue NodeId")
        if self.queue_type is not None and not isinstance(self.queue_type, str):
            raise TopologyValidationError("FanoutQueue.queue_type must be str or None")
        if self.durable is not None and not isinstance(self.durable, bool):
            raise TopologyValidationError("FanoutQueue.durable must be bool or None")
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
    Capacity metrics are derived from ``queues``. Unknown attributes remain
    explicit; a partial quorum-member sum is never presented as a complete
    total. Callers choose their own review thresholds.
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

    @property
    def queue_count(self) -> int:
        """Return the distinct direct queue target count, including dangling targets."""
        return len(self.queues)

    @property
    def durable_queue_count(self) -> int:
        """Return the number of targets with captured durable declarations."""
        return sum(queue.durable is True for queue in self.queues)

    @property
    def non_durable_queue_count(self) -> int:
        """Return the number of targets with captured non-durable declarations."""
        return sum(queue.durable is False for queue in self.queues)

    @property
    def unknown_durability_count(self) -> int:
        """Return the number of targets without captured durability evidence."""
        return sum(queue.durable is None for queue in self.queues)

    @property
    def quorum_queue_count(self) -> int:
        """Return the number of targets known to be quorum queues."""
        return sum(queue.queue_type == "quorum" for queue in self.queues)

    @property
    def unknown_queue_type_count(self) -> int:
        """Return the number of targets whose queue type is unknown."""
        return sum(queue.queue_type is None for queue in self.queues)

    @property
    def quorum_queues_without_membership_count(self) -> int:
        """Return the number of known quorum targets without captured membership."""
        return sum(queue.queue_type == "quorum" and queue.replication_factor is None for queue in self.queues)

    @property
    def known_quorum_member_count(self) -> int:
        """Sum captured quorum members, including leaders, where membership is known.

        This partial evidence remains useful when other targets are unresolved.
        It describes queue members, not disk writes or all queue replicas.
        """
        return sum(
            queue.replication_factor
            for queue in self.queues
            if queue.queue_type == "quorum" and queue.replication_factor is not None
        )

    @property
    def quorum_member_count(self) -> int | None:
        """Return the complete direct-target quorum-member sum, or ``None``.

        Completeness requires every target's type and every quorum target's
        membership to be known. An empty group or known non-quorum targets
        contribute zero. Streams and plugin types do not contribute to this
        quorum-only metric, independently of their replication behavior.
        """
        if self.unknown_queue_type_count or self.quorum_queues_without_membership_count:
            return None
        return self.known_quorum_member_count


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
                    durable=node.durable if node is not None else None,
                )
            )
        results.append(FanoutExchangeBindings(exchange=exchanges[exchange_id], queues=tuple(queues)))
    return tuple(results)
