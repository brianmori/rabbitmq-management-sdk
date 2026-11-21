"""Report binding durability and the RabbitMQ 4.3 queue declaration restriction."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from rabbitmq_management_sdk.exceptions import TopologyValidationError
from rabbitmq_management_sdk.topology.models import EdgeKind, ExchangeNode, NodeId, NodeKind, QueueNode, TopologyEdge
from rabbitmq_management_sdk.topology.ordering import edge_sort_key, node_sort_key

if TYPE_CHECKING:
    from typing import Literal

    from rabbitmq_management_sdk.topology.models import ClusterTopology


class BindingDurabilityKind(StrEnum):
    """Endpoint durability categories in the versioned exchange guides."""

    FULLY_DURABLE = "fully_durable"
    SEMI_DURABLE = "semi_durable"
    FULLY_TRANSIENT = "fully_transient"
    UNCLASSIFIED = "unclassified"
    UNKNOWN = "unknown"


_BINDING_KINDS = {
    (True, True): BindingDurabilityKind.FULLY_DURABLE,
    (True, False): BindingDurabilityKind.SEMI_DURABLE,
    (False, False): BindingDurabilityKind.FULLY_TRANSIENT,
    (False, True): BindingDurabilityKind.UNCLASSIFIED,
}


@dataclass(frozen=True, slots=True)
class BindingDurability:
    """A captured binding and the durability of each endpoint, when known."""

    binding: TopologyEdge
    source_durable: bool | None
    target_durable: bool | None

    def __post_init__(self) -> None:
        """Validate the captured edge kind and tri-state endpoint flags."""
        if not isinstance(self.binding, TopologyEdge) or self.binding.kind != EdgeKind.BINDING:
            raise TopologyValidationError("BindingDurability.binding must be a binding TopologyEdge")
        if any(
            value is not None and not isinstance(value, bool) for value in (self.source_durable, self.target_durable)
        ):
            raise TopologyValidationError("BindingDurability endpoint durability must be bool or None")

    @property
    def source(self) -> NodeId:
        """Return the binding's source exchange identity."""
        return self.binding.source

    @property
    def target(self) -> NodeId:
        """Return the complete queue or exchange target identity."""
        return self.binding.target

    @property
    def classification(self) -> BindingDurabilityKind:
        """Classify known endpoint combinations using the exchange guides."""
        if self.source_durable is None or self.target_durable is None:
            return BindingDurabilityKind.UNKNOWN
        return _BINDING_KINDS[self.source_durable, self.target_durable]


class QueueDeclarationStatus(StrEnum):
    """Outcome under the specific RabbitMQ 4.3 default restriction."""

    REJECTED_BY_DEFAULT = "rejected_by_default"
    NOT_AFFECTED = "not_affected"
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True, slots=True)
class QueueDeclarationCompatibility:
    """Re-declaration evidence for one captured non-durable classic queue.

    The target assumes ``deprecated_features.permit.transient_nonexcl_queues``
    is false. The outcome describes this restriction alone.
    """

    queue: NodeId
    exclusive: bool | None
    target_version: Literal["4.3"] = field(default="4.3", init=False)
    assumes_default_settings: Literal[True] = field(default=True, init=False)

    def __post_init__(self) -> None:
        """Validate queue identity and optional exclusivity evidence."""
        if not isinstance(self.queue, NodeId) or self.queue.kind != NodeKind.QUEUE:
            raise TopologyValidationError("QueueDeclarationCompatibility.queue must be a queue NodeId")
        if self.exclusive is not None and not isinstance(self.exclusive, bool):
            raise TopologyValidationError("QueueDeclarationCompatibility.exclusive must be bool or None")

    @property
    def status(self) -> QueueDeclarationStatus:
        """Return the outcome of the transient non-exclusive declaration rule."""
        if self.exclusive is None:
            return QueueDeclarationStatus.INDETERMINATE
        return QueueDeclarationStatus.NOT_AFFECTED if self.exclusive else QueueDeclarationStatus.REJECTED_BY_DEFAULT


def binding_durability(topology: ClusterTopology) -> tuple[BindingDurability, ...]:
    """Describe every captured binding, including parallel and dangling edges."""
    results = []
    bindings = (edge for edge in topology.edges if edge.kind == EdgeKind.BINDING)
    for edge in sorted(bindings, key=edge_sort_key):
        source = topology.nodes_by_id.get(edge.source)
        target = topology.nodes_by_id.get(edge.target)
        results.append(
            BindingDurability(
                edge,
                source.durable if isinstance(source, ExchangeNode) else None,
                target.durable if isinstance(target, (ExchangeNode, QueueNode)) else None,
            )
        )
    return tuple(results)


def queue_declaration_compatibility(topology: ClusterTopology) -> tuple[QueueDeclarationCompatibility, ...]:
    """Assess contained non-durable classic queues against RabbitMQ 4.3 defaults."""
    # Revisit deprecation when the SDK drops support for RabbitMQ 4.2 captures.
    # Captures from 4.3 brokers that allow transient non-exclusive queues can
    # still be checked against the 4.3 default.
    candidates = (queue for queue in topology.queues if queue.queue_type == "classic" and not queue.durable)
    return tuple(
        QueueDeclarationCompatibility(queue.id, queue.exclusive)
        for queue in sorted(candidates, key=lambda queue: node_sort_key(queue.id))
    )
