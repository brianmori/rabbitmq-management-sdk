"""Immutable results from comparing two captures of one cluster."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from rabbitmq_management_sdk.topology.models import NodeId, PolicyId
from rabbitmq_management_sdk.topology.routing_contracts import RoutingVerdict

if TYPE_CHECKING:
    from rabbitmq_management_sdk.topology.models import PolicySelection, TopologyEdge
    from rabbitmq_management_sdk.topology.routing_contracts import RoutingContract, RoutingContractResult


class ComparisonGap(StrEnum):
    """Evidence that limits a category or routing outcome on one side."""

    OUTSIDE_SHARED_SCOPE = "outside_shared_scope"
    DEFINITIONS_NOT_CAPTURED = "definitions_not_captured"
    VHOSTS_NOT_CAPTURED = "vhosts_not_captured"
    QUEUES_NOT_CAPTURED = "queues_not_captured"
    EXCHANGES_NOT_CAPTURED = "exchanges_not_captured"
    PARAMETERS_NOT_CAPTURED = "parameters_not_captured"
    POLICIES_NOT_CAPTURED = "policies_not_captured"
    BINDINGS_NOT_CAPTURED = "bindings_not_captured"
    BINDING_ARGUMENTS_UNAVAILABLE = "binding_arguments_unavailable"
    AMBIGUOUS_DECLARATION = "ambiguous_declaration"
    OBSERVATIONS_NOT_CAPTURED = "observations_not_captured"
    OBSERVATION_SCOPE_INCOMPLETE = "observation_scope_incomplete"


@dataclass(frozen=True, slots=True)
class ComparisonEvidence:
    """Ordered gaps for each side of a category comparison."""

    before: tuple[ComparisonGap, ...] = ()
    after: tuple[ComparisonGap, ...] = ()

    @property
    def indeterminate(self) -> bool:
        """Whether the evidence leaves part of the comparison unresolved."""
        return bool(self.before or self.after)


@dataclass(frozen=True, slots=True)
class DeclarationChange[T: (str, NodeId, PolicyId)]:
    """Changed or unavailable captured attributes of a shared declaration."""

    identity: T
    changed_fields: tuple[str, ...]
    indeterminate_fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DeclarationComparison[T: (str, NodeId, PolicyId)]:
    """Declared identities added or removed, and attributes of shared identities."""

    added: tuple[T, ...] = ()
    removed: tuple[T, ...] = ()
    attributes: tuple[DeclarationChange[T], ...] = ()
    evidence: ComparisonEvidence = field(default_factory=ComparisonEvidence)

    @property
    def indeterminate(self) -> bool:
        """Whether declaration coverage or attribute comparison is incomplete."""
        return self.evidence.indeterminate or any(row.indeterminate_fields for row in self.attributes)


@dataclass(frozen=True, slots=True)
class BindingComparison:
    """Added and removed captured binding identities in canonical edge order."""

    added: tuple[TopologyEdge, ...] = ()
    removed: tuple[TopologyEdge, ...] = ()
    evidence: ComparisonEvidence = field(default_factory=ComparisonEvidence)

    @property
    def indeterminate(self) -> bool:
        """Whether some binding identities could not be compared."""
        return self.evidence.indeterminate


@dataclass(frozen=True, slots=True)
class PolicySelectionChange:
    """Selection evidence for one shared resource and policy kind."""

    before: PolicySelection
    after: PolicySelection


@dataclass(frozen=True, slots=True)
class PolicySelectionComparison:
    """Changed selections and pairs with an unavailable observed selector."""

    changed: tuple[PolicySelectionChange, ...] = ()
    unavailable: tuple[PolicySelectionChange, ...] = ()
    evidence: ComparisonEvidence = field(default_factory=ComparisonEvidence)

    @property
    def indeterminate(self) -> bool:
        """Whether scope or missing selectors leave any selections unresolved."""
        return self.evidence.indeterminate or bool(self.unavailable)


@dataclass(frozen=True, slots=True)
class RoutingContractOutcome:
    """One side's evaluated result or the gaps that prevented evaluation."""

    result: RoutingContractResult | None = None
    gaps: tuple[ComparisonGap, ...] = ()

    @property
    def verdict(self) -> RoutingVerdict:
        """Return the evaluated verdict, or undetermined when evidence is missing."""
        return self.result.verdict if self.result is not None else RoutingVerdict.UNDETERMINED


@dataclass(frozen=True, slots=True)
class RoutingContractComparison:
    """Before and after outcomes for one publication contract."""

    contract: RoutingContract
    before: RoutingContractOutcome
    after: RoutingContractOutcome

    @property
    def indeterminate(self) -> bool:
        """Whether either capture leaves the contract verdict undetermined."""
        return RoutingVerdict.UNDETERMINED in (self.before.verdict, self.after.verdict)


@dataclass(frozen=True, slots=True)
class CaptureComparison:
    """Same-cluster declaration, binding, policy selection, and routing changes.

    Scope tuples name caller-declared vhost projections. A None scope denotes
    an input expected to cover the cluster. Routing contracts are in caller
    order. Other rows use canonical identity, field, or edge order.
    """

    cluster_id: str
    before_vhosts: tuple[str, ...] | None
    after_vhosts: tuple[str, ...] | None
    vhosts: DeclarationComparison[str]
    queues: DeclarationComparison[NodeId]
    exchanges: DeclarationComparison[NodeId]
    shovels: DeclarationComparison[NodeId]
    regular_policies: DeclarationComparison[PolicyId]
    operator_policies: DeclarationComparison[PolicyId]
    bindings: BindingComparison
    policy_selections: PolicySelectionComparison
    routing_contracts: tuple[RoutingContractComparison, ...] = ()
