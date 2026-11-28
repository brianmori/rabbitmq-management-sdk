from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import cached_property
from typing import TYPE_CHECKING, overload

from pydantic import ValidationError

from rabbitmq_management_sdk.client.policy_selection import build_user_policy_selections
from rabbitmq_management_sdk.exceptions import (
    TopologyAnalysisError,
    TopologyDefinitionsError,
    TopologyLoadError,
    TopologyResourceSnapshotError,
)
from rabbitmq_management_sdk.resources.v4.admin.schemas.export_response import ClusterDefinitionsResponse
from rabbitmq_management_sdk.resources.v4.exchanges.schemas.exchange_response import ExchangeResponse
from rabbitmq_management_sdk.resources.v4.queues.schemas.queue_response import QueueResponse
from rabbitmq_management_sdk.topology.cycles import (
    CycleSearchResult,
    find_cyclic_components,
    find_message_loop_candidates,
    find_strongly_connected_components,
    find_structural_cycles,
    message_loop_candidates_from_complete_result,
)
from rabbitmq_management_sdk.topology.dead_letter import dead_letter_coverage
from rabbitmq_management_sdk.topology.declaration_compatibility import (
    binding_durability,
    queue_declaration_compatibility,
)
from rabbitmq_management_sdk.topology.dependencies import DirectDependencyInventory, build_direct_dependency_inventory
from rabbitmq_management_sdk.topology.exchange_routing import exchange_routing_coverage
from rabbitmq_management_sdk.topology.fanout import fanout_exchange_bindings
from rabbitmq_management_sdk.topology.models import (
    ClusterTopology,
    ConfigurationStatus,
    NodeId,
    PolicyId,
    PolicyInventory,
)
from rabbitmq_management_sdk.topology.ordering import edge_sort_key
from rabbitmq_management_sdk.topology.parser import parse_cluster_topology
from rabbitmq_management_sdk.topology.policy_inventory import policy_inventory
from rabbitmq_management_sdk.topology.queue_inventory import QueueTypeInventory, queue_type_inventory
from rabbitmq_management_sdk.topology.queue_limits import queue_limits
from rabbitmq_management_sdk.topology.quorum import QuorumQueueConfiguration, quorum_delivery_configuration
from rabbitmq_management_sdk.topology.quorum_resilience import QuorumResilienceReport, quorum_resilience
from rabbitmq_management_sdk.topology.reachability import (
    black_hole_exchanges,
    cross_vhost_shovels,
    queues_without_declared_ingress,
    shovels_with_unconfirmed_endpoints,
    shovels_with_unresolved_vhost,
    unreachable_internal_exchanges,
)
from rabbitmq_management_sdk.topology.routing_contracts import evaluate_routing_contracts
from rabbitmq_management_sdk.topology.shovel_audit import competing_shovel_consumers, shovel_safety
from rabbitmq_management_sdk.topology.vhost_permissions import VhostPermissionsInventory, vhost_permissions_inventory

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence
    from pathlib import Path

    from rabbitmq_management_sdk.topology.cycles import StronglyConnectedComponent
    from rabbitmq_management_sdk.topology.dead_letter import DeadLetterCoverage
    from rabbitmq_management_sdk.topology.declaration_compatibility import (
        BindingDurability,
        QueueDeclarationCompatibility,
    )
    from rabbitmq_management_sdk.topology.dependencies import ResourceDependencies
    from rabbitmq_management_sdk.topology.exchange_routing import ExchangeRoutingCoverage
    from rabbitmq_management_sdk.topology.fanout import FanoutExchangeBindings
    from rabbitmq_management_sdk.topology.models import (
        ExchangeNode,
        PolicyCoverage,
        QueueNode,
        ShovelNode,
        TopologyEdge,
    )
    from rabbitmq_management_sdk.topology.queue_limits import QueueLimits
    from rabbitmq_management_sdk.topology.quorum_resilience import QuorumFailureScenario
    from rabbitmq_management_sdk.topology.routing_contracts import RoutingContract, RoutingContractResult
    from rabbitmq_management_sdk.topology.shovel_audit import CompetingShovelConsumers, ShovelSafety


@dataclass(frozen=True, slots=True)
class TopologyAuditReport:
    """Findings from one immutable topology snapshot.

    Findings are facts rather than severity-ranked alerts. For example, an
    exchange without outgoing edges can be an intentional discard pattern,
    and a queue with no declared binding can still receive messages through
    RabbitMQ's default exchange. Callers apply their own operational policy
    to the returned results.
    """

    structural_cycles: CycleSearchResult
    message_loop_candidates: CycleSearchResult
    black_hole_exchanges: tuple[ExchangeNode, ...]
    unreachable_internal_exchanges: tuple[ExchangeNode, ...]
    queues_without_declared_ingress: tuple[QueueNode, ...]
    cross_vhost_shovels: tuple[ShovelNode, ...]
    shovels_with_unconfirmed_endpoints: tuple[ShovelNode, ...]
    shovels_with_unresolved_vhost: tuple[ShovelNode, ...]
    dangling_edges: tuple[TopologyEdge, ...]
    competing_shovel_consumers: tuple[CompetingShovelConsumers, ...] = ()
    fanout_exchange_bindings: tuple[FanoutExchangeBindings, ...] = ()
    queue_type_inventory: QueueTypeInventory = field(default_factory=QueueTypeInventory)
    dead_letter_coverage: tuple[DeadLetterCoverage, ...] = ()
    exchange_routing_coverage: tuple[ExchangeRoutingCoverage, ...] = ()
    quorum_delivery_configuration: tuple[QuorumQueueConfiguration, ...] = ()
    quorum_resilience: QuorumResilienceReport = field(default_factory=QuorumResilienceReport)
    direct_dependency_inventory: DirectDependencyInventory = field(default_factory=DirectDependencyInventory)
    routing_contracts: tuple[RoutingContractResult, ...] = ()
    queue_limits: tuple[QueueLimits, ...] = ()
    shovel_safety: tuple[ShovelSafety, ...] = ()
    binding_durability: tuple[BindingDurability, ...] = ()
    queue_declaration_compatibility: tuple[QueueDeclarationCompatibility, ...] = ()
    vhost_permissions_inventory: VhostPermissionsInventory = field(default_factory=VhostPermissionsInventory)
    policy_inventory: PolicyInventory = field(default_factory=PolicyInventory)


class ClusterAuditor:
    """Primary public facade for constructing and auditing cluster topology.

    Construct this class from :meth:`AdminManager.export_definitions` and
    optional normalized queue and exchange observations, or reconstruct it
    from an existing :class:`ClusterTopology` with :meth:`from_topology`. Use
    its named methods for individual findings or :meth:`audit` for a complete,
    consistent report.

    Args:
        definitions: Validated cluster-wide definitions export.
        queues: Queue observations from a complete broker snapshot. Supply
            this together with ``exchanges`` when policy-derived routing must
            be resolved or quorum placement and delivery configuration are
            needed. Selected operator policies and effective policy definitions
            enrich delivery evidence without evaluating policy patterns locally.
        exchanges: Exchange observations from the same complete broker
            snapshot. They resolve broker-selected policies and referenced
            predeclared or system exchanges omitted by definitions exports.
            Supply this together with ``queues``.
        in_cluster_amqp_hosts: AMQP URI hosts that resolve to this cluster.
        cluster_label: Optional human-readable label for reports. It does not
            replace the broker's retained cluster name or affect the
            ``internal_cluster_id`` used in node identity.
    """

    def __init__(
        self,
        definitions: ClusterDefinitionsResponse,
        *,
        queues: Collection[QueueResponse] | None = None,
        exchanges: Collection[ExchangeResponse] | None = None,
        in_cluster_amqp_hosts: Collection[str] = (),
        cluster_label: str | None = None,
    ) -> None:
        if (queues is None) != (exchanges is None):
            raise TopologyAnalysisError("queues and exchanges must be supplied together or both omitted")
        self._definitions: ClusterDefinitionsResponse | None = definitions
        self._topology = parse_cluster_topology(
            definitions,
            in_cluster_amqp_hosts=in_cluster_amqp_hosts,
            cluster_label=cluster_label,
            observed_exchanges=exchanges if exchanges is not None else (),
            observed_queues=queues if queues is not None else (),
            user_policy_selections=(
                build_user_policy_selections(
                    queues=queues,
                    exchanges=exchanges,
                    cluster_id=definitions.internal_cluster_id,
                )
                if queues is not None and exchanges is not None
                else None
            ),
        )

    @classmethod
    def from_topology(cls, topology: ClusterTopology) -> ClusterAuditor:
        """Construct an analysis facade around an existing immutable topology.

        This path does not retain or reconstruct the definitions export from
        which the topology may have been built. It validates the argument's
        domain type, but cannot establish the completeness or provenance of
        the captured evidence represented by the topology.

        Args:
            topology: Existing SDK topology to analyze without modification.

        Returns:
            An auditor whose analysis methods operate on ``topology``.

        Raises:
            TopologyAnalysisError: If ``topology`` is not a
                :class:`ClusterTopology`.
        """
        if not isinstance(topology, ClusterTopology):
            raise TopologyAnalysisError(f"topology must be ClusterTopology, got {type(topology).__name__}")

        auditor = cls.__new__(cls)
        auditor._definitions = None
        auditor._topology = topology
        return auditor

    @classmethod
    def from_files(
        cls,
        definitions_path: Path,
        *,
        queues_path: Path | None = None,
        exchanges_path: Path | None = None,
        in_cluster_amqp_hosts: Collection[str] = (),
        cluster_label: str | None = None,
    ) -> ClusterAuditor:
        """Load the definitions export and optional resource snapshots from disk.

        ``queues_path`` and ``exchanges_path`` are an all-or-nothing pair.
        Each resource file must contain a normalized JSON array of Management
        API resource records; callers flatten paginated API responses before
        passing them here.

        Definitions must contain at least one definitions section. Older or
        manual definitions may omit version and format metadata, but error
        responses and explicitly incompatible export formats are rejected.

        Raises:
            TopologyDefinitionsError: If the definitions schema or envelope
                is invalid, including single-vhost exports.
        """
        if (queues_path is None) != (exchanges_path is None):
            raise TopologyAnalysisError("queues_path and exchanges_path must be supplied together or both omitted")

        definitions_data = cls._load_json(definitions_path, "definitions export")
        try:
            definitions = ClusterDefinitionsResponse.model_validate(definitions_data)
        except ValidationError as exc:
            raise TopologyDefinitionsError(f"Definitions export {definitions_path} has an invalid schema") from exc

        queues = cls._load_resource_snapshot(queues_path, QueueResponse, "queue") if queues_path is not None else None
        exchanges = (
            cls._load_resource_snapshot(exchanges_path, ExchangeResponse, "exchange")
            if exchanges_path is not None
            else None
        )
        return cls(
            definitions,
            queues=queues,
            exchanges=exchanges,
            in_cluster_amqp_hosts=in_cluster_amqp_hosts,
            cluster_label=cluster_label,
        )

    @staticmethod
    def _load_json(path: Path, description: str) -> object:
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except (AttributeError, OSError, TypeError, ValueError, RecursionError) as exc:
            raise TopologyLoadError(f"Could not load {description} {path}") from exc

    @overload
    @classmethod
    def _load_resource_snapshot(
        cls,
        path: Path,
        model_type: type[QueueResponse],
        resource_name: str,
    ) -> tuple[QueueResponse, ...]: ...

    @overload
    @classmethod
    def _load_resource_snapshot(
        cls,
        path: Path,
        model_type: type[ExchangeResponse],
        resource_name: str,
    ) -> tuple[ExchangeResponse, ...]: ...

    @classmethod
    def _load_resource_snapshot(
        cls,
        path: Path,
        model_type: type[QueueResponse] | type[ExchangeResponse],
        resource_name: str,
    ) -> tuple[QueueResponse, ...] | tuple[ExchangeResponse, ...]:
        data = cls._load_json(path, f"{resource_name} snapshot")
        if not isinstance(data, list):
            raise TopologyResourceSnapshotError(
                f"{resource_name.capitalize()} snapshot {path} must be a JSON array of resource records"
            )
        try:
            if model_type is QueueResponse:
                return tuple(QueueResponse.model_validate(item) for item in data)
            return tuple(ExchangeResponse.model_validate(item) for item in data)
        except ValidationError as exc:
            raise TopologyResourceSnapshotError(
                f"{resource_name.capitalize()} snapshot {path} has an invalid resource schema"
            ) from exc

    @property
    def cluster_id(self) -> str | None:
        """Broker-generated stable identity from ``internal_cluster_id``."""
        return self._topology.cluster_id

    @property
    def cluster_name(self) -> str | None:
        """Broker cluster name retained from the definitions export."""
        return self._topology.cluster_name

    @property
    def cluster_label(self) -> str | None:
        """Human-readable display label for this export, when available."""
        return self._topology.cluster_label

    @property
    def topology(self) -> ClusterTopology:
        """Immutable normalized graph constructed from this audit's inputs."""
        return self._topology

    @property
    def definitions(self) -> ClusterDefinitionsResponse:
        """Return the raw validated export retained during graph construction.

        Raises:
            TopologyAnalysisError: If this auditor was constructed with
                :meth:`from_topology` and therefore has no definitions.
        """
        if self._definitions is None:
            raise TopologyAnalysisError("Definitions are unavailable for an auditor constructed from topology")
        return self._definitions

    def strongly_connected_components(self) -> tuple[StronglyConnectedComponent, ...]:
        """Return the complete SCC partition of the declared topology.

        Every declared node appears in exactly one component, including
        acyclic singleton components. Use ``component.is_cyclic`` to
        distinguish regions that contain directed cycles.
        """
        return find_strongly_connected_components(self._topology)

    def cyclic_components(self) -> tuple[StronglyConnectedComponent, ...]:
        """Return only SCCs that contain at least one directed cycle."""
        return find_cyclic_components(self._topology)

    def structural_cycles(self, *, max_cycles: int = 0) -> CycleSearchResult:
        """Return structural graph cycles, optionally capped.

        A positive ``max_cycles`` bounds the returned results; zero returns all
        cycles. Check :attr:`CycleSearchResult.truncated` before treating a
        bounded result as complete.
        """
        return find_structural_cycles(self._topology, max_cycles=max_cycles)

    def message_loop_candidates(self, *, max_cycles: int = 0) -> CycleSearchResult:
        """Return cycles containing dead-lettering or shovel re-publication.

        Results exclude direct exchange-only cycles that RabbitMQ suppresses,
        but remain conservative candidates rather than proof that every
        routing-key or header combination can loop.
        """
        return find_message_loop_candidates(self._topology, max_cycles=max_cycles)

    def black_hole_exchanges(self) -> tuple[ExchangeNode, ...]:
        """Return non-default exchanges with no captured outgoing route.

        This is a degree finding. Use ``exchange_routing_coverage`` to evaluate
        binding-key compatibility, exchange chains, and alternate exchanges.
        """
        return black_hole_exchanges(self._topology)

    def exchange_routing_coverage(self, *, routing_key: str | None = None) -> tuple[ExchangeRoutingCoverage, ...]:
        """Return per-exchange routing evidence for an optional routing key.

        ``None`` leaves key-dependent decisions undetermined; an empty string
        is a concrete key. Results include contained default and internal
        exchanges, preserve uncertainties, and leave discard intent to callers.
        A contextual absence of destinations does not imply an exchange cannot
        route other publications. Message headers are not supplied.
        """
        return exchange_routing_coverage(self._topology, routing_key=routing_key)

    def evaluate_routing_contracts(self, contracts: Sequence[RoutingContract]) -> tuple[RoutingContractResult, ...]:
        """Evaluate caller-supplied publication contracts in input order.

        Each distinct exchange and key is traced at most once in this batch.
        Results describe the captured configuration for a single publication.
        An omitted policy selection can leave a possible alternate route
        undetermined when no immediate binding is known to match.
        """
        return evaluate_routing_contracts(self._topology, contracts)

    def unreachable_internal_exchanges(self) -> tuple[ExchangeNode, ...]:
        """Return internal exchanges with no captured incoming route."""
        return unreachable_internal_exchanges(self._topology)

    def queues_without_declared_ingress(self) -> tuple[QueueNode, ...]:
        """Return queues with no captured binding or confirmed-local shovel ingress."""
        return queues_without_declared_ingress(self._topology)

    def cross_vhost_shovels(self) -> tuple[ShovelNode, ...]:
        """Return shovels whose resolved source and destination vhosts differ."""
        return cross_vhost_shovels(self._topology)

    def shovel_safety(self) -> tuple[ShovelSafety, ...]:
        """Return captured acknowledgement and per-candidate transport facts."""
        return shovel_safety(self._topology)

    def competing_shovel_consumers(self) -> tuple[CompetingShovelConsumers, ...]:
        """Return advisory groups of shovels sharing a local source queue.

        Sharing can be intentional. For ordinary queues, destinations expecting
        every message may receive only a subset. Known stream queues and
        unconfirmed source endpoints are excluded; shovel runtime state is
        not inspected.
        """
        return competing_shovel_consumers(self._topology)

    def shovels_with_unresolved_vhost(self) -> tuple[ShovelNode, ...]:
        """Return shovels for which one or both endpoint vhosts are unknown."""
        return shovels_with_unresolved_vhost(self._topology)

    def shovels_with_unconfirmed_endpoints(self) -> tuple[ShovelNode, ...]:
        """Return shovels with an endpoint not confirmed as local."""
        return shovels_with_unconfirmed_endpoints(self._topology)

    def fanout_exchange_bindings(self) -> tuple[FanoutExchangeBindings, ...]:
        """Return fanout exchanges and their distinct, directly bound queues.

        Results include exchanges without direct queue targets and preserve
        dangling queue identities. Each target carries its queue type,
        durability, and captured quorum membership count including the leader.
        Groups expose derived counts and complete or partial quorum-member
        totals. Unknown evidence remains explicit. This is configuration
        evidence for capacity review, not proof of a performance bottleneck
        or an estimate of physical disk writes.
        """
        return fanout_exchange_bindings(self._topology)

    def queue_type_inventory(self) -> QueueTypeInventory:
        """Return queues grouped by type, with per-vhost captured defaults.

        Includes empty declared vhosts and vhosts represented by resource
        nodes. Missing defaults remain ``None``. Only contained queue nodes
        contribute to counts; dangling routes do not invent queue entries.
        """
        return queue_type_inventory(self._topology)

    def dead_letter_coverage(self) -> tuple[DeadLetterCoverage, ...]:
        """Return effective DLX settings and routing evidence for every queue.

        Results distinguish absent configuration, a missing exchange, no
        matching destination, a possible destination, and undetermined routing.
        Streams are explicitly not applicable. Terminal and discard queues
        remain in the report for callers to classify.
        """
        return dead_letter_coverage(self._topology)

    def queue_limits(self) -> tuple[QueueLimits, ...]:
        """Return queue limits, stream retention, and shared DLX coverage."""
        return queue_limits(self.dead_letter_coverage())

    def streams_without_retention_limits(self) -> tuple[QueueLimits, ...]:
        """Return streams whose effective age and size limits are both unset.

        Incomplete or invalid settings leave the affected limit unknown.
        Inspect ``queue_limits()`` for the evidence behind omitted streams.
        """
        return tuple(
            limits
            for limits in self.queue_limits()
            if limits.queue_type == "stream"
            and limits.configuration.stream_max_age_ms.status == ConfigurationStatus.UNSET
            and limits.configuration.stream_max_length_bytes.status == ConfigurationStatus.UNSET
        )

    def vhost_permissions_inventory(self) -> VhostPermissionsInventory:
        """Return captured per-vhost permission declarations and queue and connection caps."""
        return vhost_permissions_inventory(self._topology)

    def policy_inventory(self) -> PolicyInventory:
        """Return declared policies and separate resource-level selection evidence."""
        return policy_inventory(self._topology)

    def policy_lookup(self, policy: PolicyId) -> PolicyCoverage | None:
        """Find a unique declaration and its confirmed selections by complete identity.

        Returns None when the inventory has no unique declaration for the key.

        Raises:
            TopologyAnalysisError: If policy is not a PolicyId.
        """
        if not isinstance(policy, PolicyId):
            raise TopologyAnalysisError(f"policy must be PolicyId, got {type(policy).__name__}")
        return self.policy_inventory().by_policy.get(policy)

    def binding_durability(self) -> tuple[BindingDurability, ...]:
        """Return endpoint durability and classification for every captured binding."""
        return binding_durability(self._topology)

    def queue_declaration_compatibility(self) -> tuple[QueueDeclarationCompatibility, ...]:
        """Assess non-durable classic queues against the RabbitMQ 4.3 default restriction."""
        return queue_declaration_compatibility(self._topology)

    @cached_property
    def _direct_dependency_inventory(self) -> DirectDependencyInventory:
        return build_direct_dependency_inventory(self._topology)

    def direct_dependencies(self, resource: NodeId) -> ResourceDependencies | None:
        """Look up captured references to one queue or exchange by full identity.

        Contains an empty reference tuple for a contained resource with no
        references. A referenced resource missing from the graph has
        ``is_present=False``. Returns ``None`` if the identity is in neither set.
        The first query builds a shared index for subsequent lookups.

        Raises:
            TopologyAnalysisError: If ``resource`` is not a NodeId.
        """
        if not isinstance(resource, NodeId):
            raise TopologyAnalysisError(f"resource must be NodeId, got {type(resource).__name__}")
        return self._direct_dependency_inventory.by_resource.get(resource)

    def direct_dependency_inventory(self) -> DirectDependencyInventory:
        """Return the ordered index of captured queue and exchange references."""
        return self._direct_dependency_inventory

    def dangling_edges(self) -> tuple[TopologyEdge, ...]:
        """Return declared routes whose source or target is absent from the export.

        The sequence is deterministic for reproducible reports and CI output.
        """
        return tuple(sorted(self._topology.dangling_edges(), key=edge_sort_key))

    def quorum_delivery_configuration(self) -> tuple[QuorumQueueConfiguration, ...]:
        """Return captured quorum settings, their evidence, and advisory facts.

        Queue observations can supply arguments when the definitions map was
        omitted. Defaults require evidence that no setting applies. Results
        describe queue-level configuration, not individual consumers or
        guaranteed cycle termination.
        """
        return quorum_delivery_configuration(self._topology)

    def quorum_resilience(
        self,
        *,
        failure_domains: Mapping[str, str] | None = None,
        scenarios: Collection[QuorumFailureScenario] = (),
    ) -> QuorumResilienceReport:
        """Return quorum majority evidence, broker concentration, and scenario impacts.

        Args:
            failure_domains: Optional mapping from exact broker node names to
                caller-defined domain names.
            scenarios: Explicit sets of failed broker nodes and/or domains.

        Returns:
            Captured and hypothetical majority counts for declared quorum
            queues. Missing data can leave a result unknown.
        """
        return quorum_resilience(self._topology, failure_domains=failure_domains, scenarios=scenarios)

    def audit(
        self,
        *,
        max_cycles: int = 0,
        failure_domains: Mapping[str, str] | None = None,
        scenarios: Collection[QuorumFailureScenario] = (),
        routing_contracts: Sequence[RoutingContract] = (),
    ) -> TopologyAuditReport:
        """Return all supported findings for this definitions snapshot.

        The report is immutable and each sequence is deterministically
        ordered, making it suitable for comparisons, policy evaluation, and
        reproducible CI output. Failure domains and scenarios are forwarded to
        :meth:`quorum_resilience`. Supply routing contracts to evaluate them
        in one batch and include their results in the report.
        """
        resilience = self.quorum_resilience(failure_domains=failure_domains, scenarios=scenarios)
        structural_cycles = self.structural_cycles(max_cycles=max_cycles)
        message_loop_candidates = (
            self.message_loop_candidates(max_cycles=max_cycles)
            if structural_cycles.truncated
            else message_loop_candidates_from_complete_result(structural_cycles)
        )
        dlx_coverage = self.dead_letter_coverage()
        return TopologyAuditReport(
            structural_cycles=structural_cycles,
            message_loop_candidates=message_loop_candidates,
            black_hole_exchanges=self.black_hole_exchanges(),
            unreachable_internal_exchanges=self.unreachable_internal_exchanges(),
            queues_without_declared_ingress=self.queues_without_declared_ingress(),
            cross_vhost_shovels=self.cross_vhost_shovels(),
            shovels_with_unconfirmed_endpoints=self.shovels_with_unconfirmed_endpoints(),
            shovels_with_unresolved_vhost=self.shovels_with_unresolved_vhost(),
            dangling_edges=self.dangling_edges(),
            competing_shovel_consumers=self.competing_shovel_consumers(),
            fanout_exchange_bindings=self.fanout_exchange_bindings(),
            queue_type_inventory=self.queue_type_inventory(),
            dead_letter_coverage=dlx_coverage,
            exchange_routing_coverage=self.exchange_routing_coverage(),
            quorum_delivery_configuration=self.quorum_delivery_configuration(),
            quorum_resilience=resilience,
            direct_dependency_inventory=self.direct_dependency_inventory(),
            routing_contracts=() if routing_contracts == () else self.evaluate_routing_contracts(routing_contracts),
            queue_limits=queue_limits(dlx_coverage),
            shovel_safety=self.shovel_safety(),
            binding_durability=self.binding_durability(),
            queue_declaration_compatibility=self.queue_declaration_compatibility(),
            vhost_permissions_inventory=self.vhost_permissions_inventory(),
            policy_inventory=self.policy_inventory(),
        )
