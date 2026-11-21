"""Calculate quorum placement and failure exposure from captured queue records."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from rabbitmq_management_sdk.exceptions import TopologyAnalysisError, TopologyValidationError
from rabbitmq_management_sdk.topology.models import QueueNode, QuorumPlacement
from rabbitmq_management_sdk.topology.ordering import node_sort_key

if TYPE_CHECKING:
    from rabbitmq_management_sdk.topology.models import ClusterTopology, NodeId


@dataclass(frozen=True, slots=True)
class QuorumQueueResilience:
    """Majority and failure margin for one captured quorum group."""

    queue: QueueNode

    def __post_init__(self) -> None:
        if not isinstance(self.queue, QueueNode) or self.queue.queue_type != "quorum":
            raise TopologyValidationError("QuorumQueueResilience.queue must be a quorum QueueNode")

    @property
    def required_majority(self) -> int | None:
        """Return the minimum number of members needed for a majority."""
        count = self.queue.replication_factor
        return None if count is None else count // 2 + 1

    @property
    def theoretical_failure_tolerance(self) -> int | None:
        """Return how many members could be lost if all were initially online."""
        count = self.queue.replication_factor
        return None if count is None else (count - 1) // 2

    @property
    def observed_failure_margin(self) -> int | None:
        """Return the reported online count minus the required majority."""
        placement = self.queue.quorum_placement
        majority = self.required_majority
        if placement is None or placement.online_members is None or majority is None:
            return None
        return len(placement.online_members) - majority

    @property
    def observed_online_majority(self) -> bool | None:
        """Return whether the capture reports at least the required majority."""
        margin = self.observed_failure_margin
        return None if margin is None else margin >= 0


@dataclass(frozen=True, slots=True)
class QuorumBrokerConcentration:
    """Known quorum member and leader placements on a single broker."""

    broker: str
    failure_domain: str | None
    member_queues: tuple[NodeId, ...]
    leader_queues: tuple[NodeId, ...]

    @property
    def member_queue_count(self) -> int:
        return len(self.member_queues)

    @property
    def leader_queue_count(self) -> int:
        return len(self.leader_queues)


@dataclass(frozen=True, slots=True)
class QuorumFailureScenario:
    """Simultaneous failure of explicit broker nodes and caller-defined domains."""

    failed_nodes: tuple[str, ...] = ()
    failed_domains: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name, names in (("failed_nodes", self.failed_nodes), ("failed_domains", self.failed_domains)):
            if not isinstance(names, tuple) or any(not isinstance(name, str) or not name for name in names):
                raise TopologyValidationError(f"QuorumFailureScenario.{field_name} must be a tuple of nonempty strings")
            object.__setattr__(self, field_name, tuple(sorted(set(names))))


@dataclass(frozen=True, slots=True)
class QuorumQueueFailureImpact:
    """Scenario impact on one captured group, using its original majority."""

    queue: NodeId
    affected: bool | None
    leader_failed: bool | None
    failed_members: tuple[str, ...] | None
    unmapped_members: tuple[str, ...] | None
    member_majority_after_failure: bool | None
    observed_failure_margin_after_failure: int | None

    @property
    def observed_online_majority_after_failure(self) -> bool | None:
        """Return whether the surviving reported online members form a majority."""
        margin = self.observed_failure_margin_after_failure
        return None if margin is None else margin >= 0


@dataclass(frozen=True, slots=True)
class QuorumFailureScenarioResult:
    """Effects of a scenario on all declared quorum queues."""

    scenario: QuorumFailureScenario
    queues: tuple[QuorumQueueFailureImpact, ...]

    @property
    def affected_queues(self) -> tuple[NodeId, ...]:
        """Return queues with at least one confirmed affected replica."""
        return tuple(impact.queue for impact in self.queues if impact.affected is True)


@dataclass(frozen=True, slots=True)
class QuorumResilienceReport:
    """Immutable queue, broker, and explicit failure-scenario results."""

    queues: tuple[QuorumQueueResilience, ...] = ()
    brokers: tuple[QuorumBrokerConcentration, ...] = ()
    scenarios: tuple[QuorumFailureScenarioResult, ...] = ()


def _broker_concentration(
    queues: tuple[QuorumQueueResilience, ...], domains: Mapping[str, str]
) -> tuple[QuorumBrokerConcentration, ...]:
    members: defaultdict[str, list[NodeId]] = defaultdict(list)
    leaders: defaultdict[str, list[NodeId]] = defaultdict(list)
    for result in queues:
        queue = result.queue
        if (placement := queue.quorum_placement) is None:
            continue
        for broker in placement.members or ():
            members[broker].append(queue.id)
        if placement.leader is not None:
            leaders[placement.leader].append(queue.id)
    return tuple(
        QuorumBrokerConcentration(broker, domains.get(broker), tuple(members[broker]), tuple(leaders[broker]))
        for broker in sorted(members.keys() | leaders.keys())
    )


def _queue_failure_impact(
    result: QuorumQueueResilience,
    failed_brokers: set[str],
    failed_domains: frozenset[str],
    domains: Mapping[str, str],
) -> QuorumQueueFailureImpact:
    placement = result.queue.quorum_placement or QuorumPlacement()
    has_failures = bool(failed_brokers or failed_domains)

    def failure(broker: str) -> bool | None:
        if broker in failed_brokers:
            return True
        if failed_domains and broker not in domains:
            return None
        return False

    leader_failed = failure(placement.leader) if placement.leader is not None else (None if has_failures else False)
    member_failures = {broker: failure(broker) for broker in placement.members or ()}
    failed_members = tuple(broker for broker, failed in member_failures.items() if failed is True)
    unmapped_members = tuple(broker for broker, failed in member_failures.items() if failed is None)
    online_failures = tuple(failure(broker) for broker in placement.online_members or ())

    affected: bool | None = False
    if failed_members or leader_failed or True in online_failures:
        affected = True
    elif has_failures and (not placement.members or unmapped_members):
        affected = None

    majority = result.required_majority
    member_majority = None
    online_margin = None
    if majority is not None:
        if not has_failures:
            member_majority = True
        elif placement.members and not unmapped_members:
            member_majority = len(placement.members) - len(failed_members) >= majority
        if placement.online_members is not None and None not in online_failures:
            online_margin = online_failures.count(False) - majority

    return QuorumQueueFailureImpact(
        queue=result.queue.id,
        affected=affected,
        leader_failed=leader_failed,
        failed_members=failed_members if placement.members else None,
        unmapped_members=unmapped_members if placement.members else None,
        member_majority_after_failure=member_majority,
        observed_failure_margin_after_failure=online_margin,
    )


def quorum_resilience(
    topology: ClusterTopology,
    *,
    failure_domains: Mapping[str, str] | None = None,
    scenarios: Collection[QuorumFailureScenario] = (),
) -> QuorumResilienceReport:
    """Summarize captured placement and evaluate the explicitly supplied losses."""
    domains: dict[str, str] = {}
    if failure_domains is not None:
        if not isinstance(failure_domains, Mapping):
            raise TopologyAnalysisError("failure_domains must be a mapping from broker names to domain names")
        domains = dict(failure_domains)
        if any(not isinstance(name, str) or not name for item in domains.items() for name in item):
            raise TopologyAnalysisError("failure_domains must map nonempty broker names to nonempty domain names")
    if (
        not isinstance(scenarios, Collection)
        or isinstance(scenarios, (str, bytes))
        or any(not isinstance(scenario, QuorumFailureScenario) for scenario in scenarios)
    ):
        raise TopologyAnalysisError("scenarios must be a collection of QuorumFailureScenario values")

    queues = tuple(
        QuorumQueueResilience(queue)
        for queue in sorted(topology.queues, key=lambda queue: node_sort_key(queue.id))
        if queue.queue_type == "quorum"
    )
    results = []
    for scenario in sorted(set(scenarios), key=lambda item: (item.failed_nodes, item.failed_domains)):
        failed_domains = frozenset(scenario.failed_domains)
        failed_brokers = set(scenario.failed_nodes)
        failed_brokers.update(broker for broker, domain in domains.items() if domain in failed_domains)
        impacts = tuple(_queue_failure_impact(queue, failed_brokers, failed_domains, domains) for queue in queues)
        results.append(QuorumFailureScenarioResult(scenario, impacts))
    return QuorumResilienceReport(queues, _broker_concentration(queues, domains), tuple(results))
