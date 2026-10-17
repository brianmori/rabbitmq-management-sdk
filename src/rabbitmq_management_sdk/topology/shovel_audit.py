"""Find shovels configured to compete for deliveries from a source queue."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from rabbitmq_management_sdk.exceptions import TopologyValidationError
from rabbitmq_management_sdk.topology.models import NodeId, NodeKind, ShovelNode
from rabbitmq_management_sdk.topology.ordering import node_sort_key

if TYPE_CHECKING:
    from rabbitmq_management_sdk.topology.models import ClusterTopology

_MIN_COMPETING_SHOVELS = 2


def _source_queue_id(shovel: ShovelNode) -> NodeId | None:
    """Resolve a fixed, confirmed-local queue source without inventing a node."""
    source = shovel.source
    if (
        not source.is_confirmed_local
        or source.resource_kind != NodeKind.QUEUE
        or source.vhost is None
        or source.resource_name is None
    ):
        return None
    return NodeId(
        cluster_id=shovel.id.cluster_id,
        vhost=source.vhost,
        name=source.resource_name,
        kind=NodeKind.QUEUE,
    )


@dataclass(frozen=True, slots=True)
class CompetingShovelConsumers:
    """An advisory group of shovels sharing one confirmed-local source queue.

    This describes captured configuration, not active consumers or proven
    message loss. Sharing can intentionally distribute work or provide
    failover. On an ordinary queue, destinations expecting every message may
    instead receive only a subset. Known stream queues are excluded by the
    audit because their consumers read non-destructively.

    Attributes:
        queue: Source queue identity, even if its declaration is absent.
        shovels: At least two distinct shovels, ordered by canonical identity.
    """

    queue: NodeId
    shovels: tuple[ShovelNode, ...]

    def __post_init__(self) -> None:
        """Validate group membership and canonicalize its order."""
        if not isinstance(self.queue, NodeId) or self.queue.kind != NodeKind.QUEUE:
            raise TopologyValidationError("CompetingShovelConsumers.queue must be a queue NodeId")
        if not isinstance(self.shovels, tuple) or len(self.shovels) < _MIN_COMPETING_SHOVELS:
            raise TopologyValidationError("CompetingShovelConsumers.shovels must be a tuple of at least two shovels")
        for shovel in self.shovels:
            if not isinstance(shovel, ShovelNode):
                raise TopologyValidationError("CompetingShovelConsumers.shovels must contain only ShovelNode values")
            if _source_queue_id(shovel) != self.queue:
                raise TopologyValidationError(
                    "CompetingShovelConsumers.shovels must share the confirmed-local source queue"
                )
        if len({shovel.id for shovel in self.shovels}) != len(self.shovels):
            raise TopologyValidationError("CompetingShovelConsumers.shovels must have distinct identities")
        object.__setattr__(self, "shovels", tuple(sorted(self.shovels, key=lambda shovel: node_sort_key(shovel.id))))


def competing_shovel_consumers(topology: ClusterTopology) -> tuple[CompetingShovelConsumers, ...]:
    """Group two or more shovels consuming from the same local source queue.

    Compare complete source queue identities, independently of the shovels'
    declaring vhosts, protocols, URI aliases, and destinations. Unconfirmed,
    unresolved, and exchange sources cannot establish a shared queue and are
    excluded. A missing queue declaration does not erase source evidence;
    its queue type is unknown and any dangling edges remain separate findings.

    Known stream queues are excluded because stream consumption is
    non-destructive. For other queues this is a warning sign requiring review,
    not a claim that all configured shovels are active or that sharing is wrong.
    """
    stream_queues = {queue.id for queue in topology.queues if queue.queue_type == "stream"}
    by_queue: dict[NodeId, list[ShovelNode]] = {}
    for shovel in topology.shovels:
        queue_id = _source_queue_id(shovel)
        if queue_id is not None and queue_id not in stream_queues:
            by_queue.setdefault(queue_id, []).append(shovel)

    return tuple(
        CompetingShovelConsumers(queue=queue_id, shovels=tuple(by_queue[queue_id]))
        for queue_id in sorted(by_queue, key=node_sort_key)
        if len(by_queue[queue_id]) >= _MIN_COMPETING_SHOVELS
    )
