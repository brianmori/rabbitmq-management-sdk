"""Index direct references to queues and exchanges in captured configuration."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum
from functools import cached_property
from types import MappingProxyType
from typing import TYPE_CHECKING

from rabbitmq_management_sdk.topology.models import EdgeKind
from rabbitmq_management_sdk.topology.ordering import node_sort_key, optional_string_sort_key
from rabbitmq_management_sdk.topology.shovel import confirmed_local_resource_id

if TYPE_CHECKING:
    from collections.abc import Mapping

    from rabbitmq_management_sdk.topology.models import ClusterTopology, NodeId


class DirectReferenceRole(StrEnum):
    """The role played by the resource being looked up.

    Dead-letter routes apply during dead-lettering. Alternate routes apply
    when the referring exchange has no matching immediate binding destination.
    """

    BINDING_SOURCE = "binding_source"
    BINDING_TARGET = "binding_target"
    DEAD_LETTER_EXCHANGE = "dead_letter_exchange"
    ALTERNATE_EXCHANGE = "alternate_exchange"
    SHOVEL_SOURCE = "shovel_source"
    SHOVEL_DESTINATION = "shovel_destination"


@dataclass(frozen=True, slots=True)
class DirectReference:
    """One captured configuration relationship to a queue or exchange.

    Attributes:
        role: How the resource is referenced.
        related: The other binding endpoint, referring queue or exchange, or
            shovel declaration, identified by its full NodeId.
        routing_key: Captured binding key, DLX override, or fixed key for a
            shovel exchange endpoint. An empty key is distinct from None.
        binding_arguments: Canonical JSON text retained for a binding.
    """

    role: DirectReferenceRole
    related: NodeId
    routing_key: str | None = None
    binding_arguments: str | None = None


@dataclass(frozen=True, slots=True)
class ResourceDependencies:
    """Direct references grouped under one queue or exchange identity.

    Attributes:
        resource: The referenced queue or exchange.
        is_present: Whether a node for this resource is contained in the
            topology, including any supplemented predeclared exchange.
        references: References ordered by role, related identity, routing key
            and binding arguments. Empty for a contained unreferenced node.
    """

    resource: NodeId
    is_present: bool
    references: tuple[DirectReference, ...]


@dataclass(frozen=True)
class DirectDependencyInventory:
    """Direct references for contained and referenced queues and exchanges.

    Resources are ordered by canonical NodeId. The read-only mapping exposes
    the same rows in the same order and provides lookup by full identity.
    """

    resources: tuple[ResourceDependencies, ...] = ()

    @cached_property
    def by_resource(self) -> Mapping[NodeId, ResourceDependencies]:
        """Return the precomputed identity lookup after its first use."""
        return MappingProxyType({row.resource: row for row in self.resources})

    def __reduce__(self) -> tuple[type[DirectDependencyInventory], tuple[tuple[ResourceDependencies, ...]]]:
        """Serialize ordered rows and let the derived mapping rebuild on use."""
        return (type(self), (self.resources,))


_TARGET_ROLES = {
    EdgeKind.BINDING: DirectReferenceRole.BINDING_TARGET,
    EdgeKind.DEAD_LETTER: DirectReferenceRole.DEAD_LETTER_EXCHANGE,
    EdgeKind.ALTERNATE_EXCHANGE: DirectReferenceRole.ALTERNATE_EXCHANGE,
}


def build_direct_dependency_inventory(topology: ClusterTopology) -> DirectDependencyInventory:
    """Group references in one pass over edges and one pass over shovels."""
    by_resource: dict[NodeId, list[DirectReference]] = defaultdict(list)
    for edge in topology.edges:
        target_role = _TARGET_ROLES.get(edge.kind)
        if target_role is None:
            continue
        by_resource[edge.target].append(DirectReference(target_role, edge.source, edge.routing_key, edge.arguments))
        if edge.kind == EdgeKind.BINDING:
            by_resource[edge.source].append(
                DirectReference(DirectReferenceRole.BINDING_SOURCE, edge.target, edge.routing_key, edge.arguments)
            )

    for shovel in topology.shovels:
        for role, endpoint in (
            (DirectReferenceRole.SHOVEL_SOURCE, shovel.source),
            (DirectReferenceRole.SHOVEL_DESTINATION, shovel.destination),
        ):
            resource = confirmed_local_resource_id(endpoint, cluster_id=topology.cluster_id)
            if resource is not None:
                by_resource[resource].append(DirectReference(role, shovel.id, endpoint.routing_key))

    resource_ids = by_resource.keys() | {node.id for node in topology.queues} | {node.id for node in topology.exchanges}
    return DirectDependencyInventory(
        tuple(
            ResourceDependencies(
                resource=resource,
                is_present=resource in topology.all_node_ids,
                references=tuple(
                    sorted(
                        by_resource.get(resource, ()),
                        key=lambda reference: (
                            reference.role.value,
                            node_sort_key(reference.related),
                            optional_string_sort_key(reference.routing_key),
                            optional_string_sort_key(reference.binding_arguments),
                        ),
                    )
                ),
            )
            for resource in sorted(resource_ids, key=node_sort_key)
        )
    )
