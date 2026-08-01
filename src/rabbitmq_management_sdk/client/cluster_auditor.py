from __future__ import annotations

import json
from dataclasses import dataclass
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
from rabbitmq_management_sdk.topology.ordering import edge_sort_key
from rabbitmq_management_sdk.topology.parser import parse_cluster_topology
from rabbitmq_management_sdk.topology.reachability import (
    black_hole_exchanges,
    cross_vhost_shovels,
    queues_without_declared_ingress,
    shovels_with_unconfirmed_endpoints,
    shovels_with_unresolved_vhost,
    unreachable_internal_exchanges,
)

if TYPE_CHECKING:
    from collections.abc import Collection
    from pathlib import Path

    from rabbitmq_management_sdk.topology.cycles import StronglyConnectedComponent
    from rabbitmq_management_sdk.topology.models import (
        ClusterTopology,
        ExchangeNode,
        QueueNode,
        ShovelNode,
        TopologyEdge,
    )


@dataclass(frozen=True, slots=True)
class TopologyAuditReport:
    """Structural findings from one immutable topology snapshot.

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


class ClusterAuditor:
    """Primary public facade for auditing a RabbitMQ definitions export.

    Construct this class from :meth:`AdminManagerV4.export_definitions` and
    optional normalized queue and exchange observations. Use its named
    methods for individual findings or :meth:`audit` for a complete,
    consistent report.

    Args:
        definitions: Validated cluster-wide definitions export.
        queues: Queue observations from a complete broker snapshot. Supply
            this together with ``exchanges`` when policy-derived routing must
            be resolved.
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
        self._definitions = definitions
        self._topology = parse_cluster_topology(
            definitions,
            in_cluster_amqp_hosts=in_cluster_amqp_hosts,
            cluster_label=cluster_label,
            observed_exchanges=exchanges if exchanges is not None else (),
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
        except (AttributeError, OSError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
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
        """Raw validated export, for callers needing data parse_cluster_topology
        doesn't include into the graph (users, permissions, policies, ...)."""
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
        """Return non-default exchanges with no captured outgoing route."""
        return black_hole_exchanges(self._topology)

    def unreachable_internal_exchanges(self) -> tuple[ExchangeNode, ...]:
        """Return internal exchanges with no captured incoming route."""
        return unreachable_internal_exchanges(self._topology)

    def queues_without_declared_ingress(self) -> tuple[QueueNode, ...]:
        """Return queues with no captured binding or confirmed-local shovel ingress."""
        return queues_without_declared_ingress(self._topology)

    def cross_vhost_shovels(self) -> tuple[ShovelNode, ...]:
        """Return shovels whose resolved source and destination vhosts differ."""
        return cross_vhost_shovels(self._topology)

    def shovels_with_unresolved_vhost(self) -> tuple[ShovelNode, ...]:
        """Return shovels for which one or both endpoint vhosts are unknown."""
        return shovels_with_unresolved_vhost(self._topology)

    def shovels_with_unconfirmed_endpoints(self) -> tuple[ShovelNode, ...]:
        """Return shovels with an endpoint not confirmed as local."""
        return shovels_with_unconfirmed_endpoints(self._topology)

    def dangling_edges(self) -> tuple[TopologyEdge, ...]:
        """Return declared routes whose source or target is absent from the export.

        The sequence is deterministic for reproducible reports and CI output.
        """
        return tuple(sorted(self._topology.dangling_edges(), key=edge_sort_key))

    def audit(self, *, max_cycles: int = 0) -> TopologyAuditReport:
        """Return all supported findings for this definitions snapshot.

        The report is immutable and each sequence is deterministically
        ordered, making it suitable for comparisons, policy evaluation, and
        reproducible CI output.
        """
        structural_cycles = self.structural_cycles(max_cycles=max_cycles)
        message_loop_candidates = (
            self.message_loop_candidates(max_cycles=max_cycles)
            if structural_cycles.truncated
            else message_loop_candidates_from_complete_result(structural_cycles)
        )
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
        )
