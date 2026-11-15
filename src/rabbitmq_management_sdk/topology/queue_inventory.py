"""Group captured queues by type without inferring undeclared resources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from rabbitmq_management_sdk.exceptions import TopologyValidationError
from rabbitmq_management_sdk.topology.models import QueueNode
from rabbitmq_management_sdk.topology.ordering import node_sort_key

if TYPE_CHECKING:
    from collections.abc import Iterable

    from rabbitmq_management_sdk.topology.models import ClusterTopology


@dataclass(frozen=True, slots=True)
class QueueTypeGroup:
    """Queues of one resolved type, ordered by complete resource identity.

    Type names are open strings. Only types present in the captured queues
    are emitted by the inventory; vhost defaults do not create empty groups.
    """

    queue_type: str
    queues: tuple[QueueNode, ...]

    def __post_init__(self) -> None:
        """Validate queue identities and normalize their order."""
        if not isinstance(self.queue_type, str):
            raise TopologyValidationError("QueueTypeGroup.queue_type must be str")
        if not isinstance(self.queues, tuple) or any(not isinstance(queue, QueueNode) for queue in self.queues):
            raise TopologyValidationError("QueueTypeGroup.queues must be a tuple of QueueNode values")
        if any(queue.queue_type != self.queue_type for queue in self.queues):
            raise TopologyValidationError("QueueTypeGroup queues must match the group queue type")
        if len({queue.id for queue in self.queues}) != len(self.queues):
            raise TopologyValidationError("QueueTypeGroup queues must have distinct identities")
        if len({queue.id.cluster_id for queue in self.queues}) > 1:
            raise TopologyValidationError("QueueTypeGroup queues must belong to one cluster")
        object.__setattr__(self, "queues", tuple(sorted(self.queues, key=lambda queue: node_sort_key(queue.id))))

    @property
    def queue_count(self) -> int:
        """Return the number of queues of this type."""
        return len(self.queues)


@dataclass(frozen=True, slots=True)
class VhostQueueInventory:
    """Queue types and the captured default for one virtual host.

    ``default_queue_type=None`` means the default is unknown. Existing queue
    types can differ from that default. Groups are ordered by type name.
    """

    vhost: str
    default_queue_type: str | None
    queue_types: tuple[QueueTypeGroup, ...]

    def __post_init__(self) -> None:
        """Validate the vhost scope and normalize type order."""
        if not isinstance(self.vhost, str):
            raise TopologyValidationError("VhostQueueInventory.vhost must be str")
        if self.default_queue_type is not None and not isinstance(self.default_queue_type, str):
            raise TopologyValidationError("VhostQueueInventory.default_queue_type must be str or None")
        if not isinstance(self.queue_types, tuple) or any(
            not isinstance(group, QueueTypeGroup) for group in self.queue_types
        ):
            raise TopologyValidationError("VhostQueueInventory.queue_types must be a tuple of QueueTypeGroup values")
        if len({group.queue_type for group in self.queue_types}) != len(self.queue_types):
            raise TopologyValidationError("VhostQueueInventory queue types must be distinct")
        queues = tuple(queue for group in self.queue_types for queue in group.queues)
        if len({queue.id for queue in queues}) != len(queues):
            raise TopologyValidationError("VhostQueueInventory queues must have distinct identities")
        if any(queue.id.vhost != self.vhost for queue in queues):
            raise TopologyValidationError("VhostQueueInventory queues must belong to its vhost")
        if len({queue.id.cluster_id for queue in queues}) > 1:
            raise TopologyValidationError("VhostQueueInventory queues must belong to one cluster")
        object.__setattr__(self, "queue_types", tuple(sorted(self.queue_types, key=lambda group: group.queue_type)))

    @property
    def queue_count(self) -> int:
        """Return the number of queues in this vhost."""
        return sum(group.queue_count for group in self.queue_types)


@dataclass(frozen=True, slots=True)
class QueueTypeInventory:
    """Cluster-wide and per-vhost inventory of captured queue declarations.

    Virtual hosts are ordered by name. Cluster totals and type groups are
    derived from those entries, so they cannot contradict the per-vhost data.
    No routing or runtime activity is inferred.
    """

    vhosts: tuple[VhostQueueInventory, ...] = ()

    def __post_init__(self) -> None:
        """Validate cluster scope and normalize vhost order."""
        if not isinstance(self.vhosts, tuple) or any(
            not isinstance(vhost, VhostQueueInventory) for vhost in self.vhosts
        ):
            raise TopologyValidationError("QueueTypeInventory.vhosts must be a tuple of VhostQueueInventory values")
        if len({vhost.vhost for vhost in self.vhosts}) != len(self.vhosts):
            raise TopologyValidationError("QueueTypeInventory vhosts must have distinct names")
        if (
            len({queue.id.cluster_id for vhost in self.vhosts for group in vhost.queue_types for queue in group.queues})
            > 1
        ):
            raise TopologyValidationError("QueueTypeInventory queues must belong to one cluster")
        object.__setattr__(self, "vhosts", tuple(sorted(self.vhosts, key=lambda vhost: vhost.vhost)))

    @property
    def queue_count(self) -> int:
        """Return the total number of captured queues."""
        return sum(vhost.queue_count for vhost in self.vhosts)

    @property
    def queue_types(self) -> tuple[QueueTypeGroup, ...]:
        """Return cluster-wide type groups ordered by type name."""
        return _group_by_type(queue for vhost in self.vhosts for group in vhost.queue_types for queue in group.queues)


def _group_by_type(queues: Iterable[QueueNode]) -> tuple[QueueTypeGroup, ...]:
    by_type: dict[str, list[QueueNode]] = {}
    for queue in queues:
        by_type.setdefault(queue.queue_type, []).append(queue)
    return tuple(QueueTypeGroup(queue_type, tuple(by_type[queue_type])) for queue_type in sorted(by_type))


def queue_type_inventory(topology: ClusterTopology) -> QueueTypeInventory:
    """Inventory declared queues and retain empty captured virtual hosts.

    Resource vhosts absent from the metadata are included with an unknown
    default. Dangling routes and shovel endpoints do not invent queues or
    virtual host declarations.
    """
    defaults = {vhost.name: vhost.default_queue_type for vhost in topology.vhosts}
    vhost_names = defaults.keys() | {node.id.vhost for node in topology.nodes}
    queues_by_vhost: dict[str, list[QueueNode]] = {name: [] for name in vhost_names}
    for queue in topology.queues:
        queues_by_vhost[queue.id.vhost].append(queue)
    return QueueTypeInventory(
        vhosts=tuple(
            VhostQueueInventory(name, defaults.get(name), _group_by_type(queues_by_vhost[name]))
            for name in sorted(vhost_names)
        )
    )
