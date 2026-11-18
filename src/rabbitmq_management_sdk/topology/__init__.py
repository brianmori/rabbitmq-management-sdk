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
from rabbitmq_management_sdk.topology.dead_letter import (
    DeadLetterCoverage,
    DeadLetterCoverageStatus,
    DeadLetterRoutingIssue,
    DeadLetterUncertainty,
)
from rabbitmq_management_sdk.topology.declaration_compatibility import (
    BindingDurability,
    BindingDurabilityKind,
    QueueDeclarationCompatibility,
    QueueDeclarationStatus,
)
from rabbitmq_management_sdk.topology.dependencies import (
    DirectDependencyInventory,
    DirectReference,
    DirectReferenceRole,
    ResourceDependencies,
)
from rabbitmq_management_sdk.topology.exchange_routing import ExchangeRoutingCoverage
from rabbitmq_management_sdk.topology.fanout import FanoutExchangeBindings, FanoutQueue
from rabbitmq_management_sdk.topology.models import (
    ClusterTopology,
    ConfigurationEvidence,
    ConfigurationSource,
    ConfigurationStatus,
    ConfigurationUncertainty,
    ConfigurationValue,
    EdgeKind,
    EndpointAuthority,
    ExchangeNode,
    NodeId,
    NodeKind,
    QueueLimitsConfiguration,
    QueueNode,
    QuorumDeliveryConfiguration,
    QuorumPlacement,
    ResourceEndpoint,
    ShovelAckMode,
    ShovelAcknowledgement,
    ShovelAckSource,
    ShovelAckTiming,
    ShovelEndpointTransport,
    ShovelNode,
    ShovelTransport,
    TopologyEdge,
    TopologyNode,
    VirtualHostMetadata,
)
from rabbitmq_management_sdk.topology.ordering import (
    EdgeSortKey,
    NodeSortKey,
    edge_sort_key,
    node_sort_key,
)
from rabbitmq_management_sdk.topology.queue_inventory import QueueTypeGroup, QueueTypeInventory, VhostQueueInventory
from rabbitmq_management_sdk.topology.queue_limits import QueueLimits
from rabbitmq_management_sdk.topology.quorum import QuorumConfigurationIssue, QuorumQueueConfiguration
from rabbitmq_management_sdk.topology.quorum_resilience import (
    QuorumBrokerConcentration,
    QuorumFailureScenario,
    QuorumFailureScenarioResult,
    QuorumQueueFailureImpact,
    QuorumQueueResilience,
    QuorumResilienceReport,
)
from rabbitmq_management_sdk.topology.routing import RoutingIssue, RoutingStatus, RoutingUncertainty
from rabbitmq_management_sdk.topology.routing_contracts import (
    RoutingCondition,
    RoutingConditionKind,
    RoutingConditionResult,
    RoutingContract,
    RoutingContractResult,
    RoutingOriginStatus,
    RoutingVerdict,
)
from rabbitmq_management_sdk.topology.shovel_audit import CompetingShovelConsumers, ShovelSafety

__all__ = [
    "BindingDurability",
    "BindingDurabilityKind",
    "ClusterTopology",
    "CompetingShovelConsumers",
    "ConfigurationEvidence",
    "ConfigurationSource",
    "ConfigurationStatus",
    "ConfigurationUncertainty",
    "ConfigurationValue",
    "Cycle",
    "CycleSearchResult",
    "DeadLetterCoverage",
    "DeadLetterCoverageStatus",
    "DeadLetterRoutingIssue",
    "DeadLetterUncertainty",
    "DirectDependencyInventory",
    "DirectReference",
    "DirectReferenceRole",
    "EdgeKind",
    "EdgeSortKey",
    "EndpointAuthority",
    "ExchangeNode",
    "ExchangeRoutingCoverage",
    "FanoutExchangeBindings",
    "FanoutQueue",
    "NodeId",
    "NodeKind",
    "NodeSortKey",
    "QueueDeclarationCompatibility",
    "QueueDeclarationStatus",
    "QueueLimits",
    "QueueLimitsConfiguration",
    "QueueNode",
    "QueueTypeGroup",
    "QueueTypeInventory",
    "QuorumBrokerConcentration",
    "QuorumConfigurationIssue",
    "QuorumDeliveryConfiguration",
    "QuorumFailureScenario",
    "QuorumFailureScenarioResult",
    "QuorumPlacement",
    "QuorumQueueConfiguration",
    "QuorumQueueFailureImpact",
    "QuorumQueueResilience",
    "QuorumResilienceReport",
    "ResourceDependencies",
    "ResourceEndpoint",
    "RoutingCondition",
    "RoutingConditionKind",
    "RoutingConditionResult",
    "RoutingContract",
    "RoutingContractResult",
    "RoutingIssue",
    "RoutingOriginStatus",
    "RoutingStatus",
    "RoutingUncertainty",
    "RoutingVerdict",
    "ShovelAckMode",
    "ShovelAckSource",
    "ShovelAckTiming",
    "ShovelAcknowledgement",
    "ShovelEndpointTransport",
    "ShovelNode",
    "ShovelSafety",
    "ShovelTransport",
    "StronglyConnectedComponent",
    "TopologyEdge",
    "TopologyNode",
    "VhostQueueInventory",
    "VirtualHostMetadata",
    "edge_sort_key",
    "node_sort_key",
]
