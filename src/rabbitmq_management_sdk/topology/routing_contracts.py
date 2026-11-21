"""Evaluate caller-defined queue conditions using the shared exchange router."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from rabbitmq_management_sdk.exceptions import TopologyAnalysisError, TopologyValidationError
from rabbitmq_management_sdk.topology.models import NodeId, NodeKind
from rabbitmq_management_sdk.topology.ordering import node_sort_key
from rabbitmq_management_sdk.topology.routing import RoutingIssue, RoutingUncertainty, _ExchangeRouter, _RoutingEvidence

if TYPE_CHECKING:
    from collections.abc import Iterable

    from rabbitmq_management_sdk.topology.models import ClusterTopology


class RoutingConditionKind(StrEnum):
    """Set operations supported by publication routing contracts."""

    REACHES_ALL = "reaches_all"
    REACHES_ANY = "reaches_any"
    AVOIDS = "avoids"
    ONLY_WITHIN = "only_within"
    EXACTLY = "exactly"


@dataclass(frozen=True, slots=True)
class RoutingCondition:
    """A predicate over the destination queue identities of one publication."""

    kind: RoutingConditionKind
    queues: frozenset[NodeId]

    def __post_init__(self) -> None:
        if not isinstance(self.kind, RoutingConditionKind):
            raise TopologyValidationError("RoutingCondition.kind must be RoutingConditionKind")
        if not isinstance(self.queues, frozenset) or any(
            not isinstance(queue, NodeId) or queue.kind != NodeKind.QUEUE for queue in self.queues
        ):
            raise TopologyValidationError("RoutingCondition.queues must be a frozenset of queue NodeId values")
        if self.kind in {RoutingConditionKind.REACHES_ALL, RoutingConditionKind.REACHES_ANY} and not self.queues:
            raise TopologyValidationError(f"{self.kind} requires at least one queue")

    @classmethod
    def reaches_all(cls, queues: Iterable[NodeId]) -> RoutingCondition:
        """Require every selected queue to be a destination."""
        return cls(RoutingConditionKind.REACHES_ALL, frozenset(queues))

    @classmethod
    def reaches_any(cls, queues: Iterable[NodeId]) -> RoutingCondition:
        """Require at least one selected queue to be a destination."""
        return cls(RoutingConditionKind.REACHES_ANY, frozenset(queues))

    @classmethod
    def avoids(cls, queues: Iterable[NodeId]) -> RoutingCondition:
        """Exclude all selected queues from the destinations."""
        return cls(RoutingConditionKind.AVOIDS, frozenset(queues))

    @classmethod
    def only_within(cls, queues: Iterable[NodeId]) -> RoutingCondition:
        """Allow any subset of the selected queues, including the empty set."""
        return cls(RoutingConditionKind.ONLY_WITHIN, frozenset(queues))

    @classmethod
    def exactly(cls, queues: Iterable[NodeId]) -> RoutingCondition:
        """Require exactly the selected destinations, including an empty set."""
        return cls(RoutingConditionKind.EXACTLY, frozenset(queues))


@dataclass(frozen=True, slots=True)
class RoutingContract:
    """Named conditions for one direct publication with one routing key.

    Conditions are combined with AND in caller-supplied order. An empty string
    is a concrete key. An explicitly supplied None leaves the key unspecified.
    """

    name: str
    origin: NodeId
    routing_key: str | None
    conditions: tuple[RoutingCondition, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise TopologyValidationError("RoutingContract.name must be a nonempty string")
        if not isinstance(self.origin, NodeId) or self.origin.kind != NodeKind.EXCHANGE:
            raise TopologyValidationError("RoutingContract.origin must be an exchange NodeId")
        if self.routing_key is not None and not isinstance(self.routing_key, str):
            raise TopologyValidationError("RoutingContract.routing_key must be str or None")
        if (
            not isinstance(self.conditions, tuple)
            or not self.conditions
            or any(not isinstance(condition, RoutingCondition) for condition in self.conditions)
        ):
            raise TopologyValidationError(
                "RoutingContract.conditions must be a nonempty tuple of RoutingCondition values"
            )


class RoutingVerdict(StrEnum):
    """Whether captured evidence settles a condition or contract."""

    SATISFIED = "satisfied"
    VIOLATED = "violated"
    UNDETERMINED = "undetermined"


class RoutingOriginStatus(StrEnum):
    """Whether the captured exchange could be evaluated as a client origin."""

    EVALUATED = "evaluated"
    MISSING = "missing"
    INTERNAL = "internal"


@dataclass(frozen=True, slots=True)
class RoutingConditionResult:
    """One condition and its verdict against the contract's shared evidence."""

    condition: RoutingCondition
    verdict: RoutingVerdict


@dataclass(frozen=True, slots=True)
class RoutingContractResult:
    """Publication evidence shared by all condition results.

    Established destinations and destinations known only conditionally are
    disjoint, canonically ordered tuples. An open branch leaves room for
    destinations beyond those tuples, within the origin's cluster and vhost.
    Issues can also describe uncertainty on branches with no destinations.
    """

    contract: RoutingContract
    origin_status: RoutingOriginStatus
    conditions: tuple[RoutingConditionResult, ...]
    established_destinations: tuple[NodeId, ...]
    conditional_destinations: tuple[NodeId, ...]
    open_branch: bool
    issues: tuple[RoutingIssue, ...]

    @property
    def verdict(self) -> RoutingVerdict:
        """Combine condition verdicts with AND."""
        verdicts = {result.verdict for result in self.conditions}
        if RoutingVerdict.VIOLATED in verdicts:
            return RoutingVerdict.VIOLATED
        if RoutingVerdict.UNDETERMINED in verdicts:
            return RoutingVerdict.UNDETERMINED
        return RoutingVerdict.SATISFIED


def _verdict(*, satisfied: bool, violated: bool) -> RoutingVerdict:
    if violated:
        return RoutingVerdict.VIOLATED
    return RoutingVerdict.SATISFIED if satisfied else RoutingVerdict.UNDETERMINED


def _condition_verdict(condition: RoutingCondition, origin: NodeId, evidence: _RoutingEvidence) -> RoutingVerdict:
    queues = condition.queues
    established = evidence.destinations
    conditional = evidence.conditional_destinations
    within = RoutingVerdict.SATISFIED
    if condition.kind in {RoutingConditionKind.ONLY_WITHIN, RoutingConditionKind.EXACTLY}:
        within = _verdict(
            satisfied=not evidence.open_branch and conditional <= queues,
            violated=not established <= queues,
        )
        if condition.kind == RoutingConditionKind.ONLY_WITHIN or within == RoutingVerdict.VIOLATED:
            return within

    reached = established & queues
    # Open branches can add destinations only in the publication's scope.
    possible = reached | (conditional & queues)
    if evidence.open_branch:
        possible.update(
            queue for queue in queues if (queue.vhost, queue.cluster_id) == (origin.vhost, origin.cluster_id)
        )
    if condition.kind == RoutingConditionKind.REACHES_ANY:
        return _verdict(satisfied=bool(reached), violated=not possible)
    if condition.kind == RoutingConditionKind.AVOIDS:
        return _verdict(satisfied=not possible, violated=bool(reached))

    return _verdict(satisfied=reached == queues and within == RoutingVerdict.SATISFIED, violated=possible != queues)


def _evaluate_contract(router: _ExchangeRouter, contract: RoutingContract) -> RoutingContractResult:
    exchange = router.exchanges.get(contract.origin)
    forced_verdict = None
    if exchange is None:
        origin_status = RoutingOriginStatus.MISSING
        forced_verdict = RoutingVerdict.UNDETERMINED
        evidence = _RoutingEvidence(issues={RoutingIssue(contract.origin, RoutingUncertainty.MISSING_RESOURCE)})
    elif exchange.internal:
        origin_status = RoutingOriginStatus.INTERNAL
        forced_verdict = RoutingVerdict.VIOLATED
        evidence = _RoutingEvidence()
    else:
        origin_status = RoutingOriginStatus.EVALUATED
        evidence = router.trace(contract.origin, contract.routing_key)

    return RoutingContractResult(
        contract=contract,
        origin_status=origin_status,
        conditions=tuple(
            RoutingConditionResult(
                condition,
                forced_verdict
                if forced_verdict is not None
                else _condition_verdict(condition, contract.origin, evidence),
            )
            for condition in contract.conditions
        ),
        established_destinations=tuple(sorted(evidence.destinations, key=node_sort_key)),
        conditional_destinations=tuple(
            sorted(evidence.conditional_destinations - evidence.destinations, key=node_sort_key)
        ),
        open_branch=evidence.open_branch,
        issues=tuple(sorted(evidence.issues, key=lambda issue: (node_sort_key(issue.resource), issue.reason.value))),
    )


def evaluate_routing_contracts(
    topology: ClusterTopology, contracts: Sequence[RoutingContract]
) -> tuple[RoutingContractResult, ...]:
    """Evaluate one batch, sharing graph indexes and cached origin/key traces."""
    if (
        not isinstance(contracts, Sequence)
        or isinstance(contracts, (str, bytes))
        or any(not isinstance(contract, RoutingContract) for contract in contracts)
    ):
        raise TopologyAnalysisError("contracts must be a sequence of RoutingContract values")
    if not contracts:
        return ()
    router = _ExchangeRouter(topology)
    return tuple(_evaluate_contract(router, contract) for contract in contracts)
