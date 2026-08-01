"""Supported immutable RabbitMQ topology graph and analysis vocabulary.

Use :class:`rabbitmq_management_sdk.ClusterAuditor` to construct a graph from
validated in-memory responses or captured JSON files. Only the names exported
here are supported; parser and algorithm submodules remain implementation
details. Each graph edge is a configured possible message hop, not a guarantee
that a particular message traverses it at runtime.
"""

from rabbitmq_management_sdk.topology.cycles import (
    Cycle,
    CycleSearchResult,
    StronglyConnectedComponent,
)
from rabbitmq_management_sdk.topology.models import (
    ClusterTopology,
    EdgeKind,
    EndpointAuthority,
    ExchangeNode,
    NodeId,
    NodeKind,
    QueueNode,
    ResourceEndpoint,
    ShovelNode,
    TopologyEdge,
    TopologyNode,
)

__all__ = [
    "ClusterTopology",
    "Cycle",
    "CycleSearchResult",
    "EdgeKind",
    "EndpointAuthority",
    "ExchangeNode",
    "NodeId",
    "NodeKind",
    "QueueNode",
    "ResourceEndpoint",
    "ShovelNode",
    "StronglyConnectedComponent",
    "TopologyEdge",
    "TopologyNode",
]
