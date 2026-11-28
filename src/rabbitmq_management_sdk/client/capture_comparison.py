"""Compare retained definitions, policy observations, and routing evidence offline."""

from __future__ import annotations

import json
from collections.abc import Collection
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from rabbitmq_management_sdk.client._capture_sanitization import _SHOVEL_SETTING_TYPES, CaptureSanitizer
from rabbitmq_management_sdk.client.audit_capture import AuditCapture
from rabbitmq_management_sdk.client.cluster_auditor import ClusterAuditor
from rabbitmq_management_sdk.exceptions import TopologyAnalysisError
from rabbitmq_management_sdk.topology.comparison import (
    BindingComparison,
    CaptureComparison,
    ComparisonEvidence,
    ComparisonGap,
    DeclarationChange,
    DeclarationComparison,
    PolicySelectionChange,
    PolicySelectionComparison,
    RoutingContractComparison,
    RoutingContractOutcome,
)
from rabbitmq_management_sdk.topology.models import NodeId, NodeKind, PolicyId, PolicyKind, PolicySelectionStatus
from rabbitmq_management_sdk.topology.ordering import edge_sort_key, node_sort_key
from rabbitmq_management_sdk.topology.parser import _binding_edge
from rabbitmq_management_sdk.topology.routing_contracts import _validate_contracts, evaluate_routing_contracts

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from pydantic import JsonValue

    from rabbitmq_management_sdk.resources.v4.admin.schemas.export_response import ClusterDefinitionsResponse
    from rabbitmq_management_sdk.topology.models import PolicySelection, TopologyEdge
    from rabbitmq_management_sdk.topology.ordering import NodeSortKey
    from rabbitmq_management_sdk.topology.routing_contracts import RoutingContract


type _Attributes = tuple[tuple[str, str | None], ...]
type _Vhosts = frozenset[str] | None

_SECTIONS = {
    "vhosts": ComparisonGap.VHOSTS_NOT_CAPTURED,
    "queues": ComparisonGap.QUEUES_NOT_CAPTURED,
    "exchanges": ComparisonGap.EXCHANGES_NOT_CAPTURED,
    "parameters": ComparisonGap.PARAMETERS_NOT_CAPTURED,
    "policies": ComparisonGap.POLICIES_NOT_CAPTURED,
    "bindings": ComparisonGap.BINDINGS_NOT_CAPTURED,
}


def _scope(values: Collection[str] | None) -> _Vhosts:
    if values is None:
        return None
    if (
        not isinstance(values, Collection)
        or isinstance(values, (str, bytes))
        or any(not isinstance(value, str) for value in values)
    ):
        raise TopologyAnalysisError("Capture vhost scope must be a collection of vhost names or None")
    return frozenset(values)


def _in_scope(vhost: str, scope: _Vhosts) -> bool:
    return scope is None or vhost in scope


@dataclass(slots=True)
class _Input:
    auditor: ClusterAuditor
    vhosts: _Vhosts

    @property
    def definitions(self) -> ClusterDefinitionsResponse | None:
        return self.auditor._comparison_definitions

    def gaps(self, *sections: str) -> tuple[ComparisonGap, ...]:
        definitions = self.definitions
        if definitions is None:
            return (ComparisonGap.DEFINITIONS_NOT_CAPTURED,)
        return tuple(_SECTIONS[section] for section in sections if section not in definitions.model_fields_set)

    def observation_gaps(self) -> tuple[ComparisonGap, ...]:
        if not self.auditor._observations_supplied:
            return (ComparisonGap.OBSERVATIONS_NOT_CAPTURED,)
        if self.auditor._observations_scope_limited:
            return (ComparisonGap.OBSERVATION_SCOPE_INCOMPLETE,)
        return ()

    def binding_rows(self, cluster_id: str | None, scope: _Vhosts) -> tuple[set[TopologyEdge], set[TopologyEdge]]:
        edges: set[TopologyEdge] = set()
        unavailable: set[TopologyEdge] = set()
        sanitizer = CaptureSanitizer()
        if self.definitions is not None:
            for row in self.definitions.bindings:
                if not _in_scope(row.vhost, scope):
                    continue
                edge = _binding_edge(row, cluster_id)
                if "arguments" not in row.model_fields_set or sanitizer.has_opaque_values(row.arguments.model_dump()):
                    unavailable.add(edge)
                else:
                    edges.add(edge)
        return edges, unavailable


def _input(value: ClusterAuditor | AuditCapture, vhosts: Collection[str] | None) -> _Input:
    if isinstance(value, AuditCapture):
        auditor = value.to_auditor()
        if value.manifest.cluster_id and value.manifest.cluster_id != auditor.cluster_id:
            raise TopologyAnalysisError("Audit capture manifest and definitions disagree on the cluster ID")
    elif isinstance(value, ClusterAuditor):
        auditor = value
    else:
        raise TopologyAnalysisError("Comparison inputs must be ClusterAuditor or AuditCapture values")
    result = _Input(auditor, _scope(vhosts))
    if result.definitions is not None:
        ids = {
            cluster_id for row in result.definitions.global_parameters if (cluster_id := row.as_internal_cluster_id())
        }
        if ids and ids != {auditor.cluster_id}:
            raise TopologyAnalysisError("Capture definitions contain conflicting cluster IDs")
    return result


def _cluster_id(before: _Input, after: _Input, mapping: str | None) -> str:
    if mapping is not None and (not isinstance(mapping, str) or not mapping.strip()):
        raise TopologyAnalysisError("cluster_id must be a nonempty string")
    before_id, after_id = before.auditor.cluster_id, after.auditor.cluster_id
    if before_id and after_id and before_id != after_id:
        raise TopologyAnalysisError("Comparison inputs have conflicting cluster IDs")
    captured = before_id or after_id
    if mapping is not None and captured and mapping != captured:
        raise TopologyAnalysisError("The cluster_id mapping conflicts with a captured cluster ID")
    if mapping is not None:
        return mapping
    if before_id and after_id:
        return before_id
    raise TopologyAnalysisError("A cluster_id mapping is required when either capture lacks a cluster ID")


def _attribute_values(
    values: dict[str, JsonValue], fields: tuple[str, ...], sanitizer: CaptureSanitizer
) -> _Attributes:
    return tuple(
        (
            name,
            json.dumps(values[name], sort_keys=True)
            if name in values and not sanitizer.has_opaque_values(values[name])
            else None,
        )
        for name in fields
    )


def _shovel_attributes(value: JsonValue, sanitizer: CaptureSanitizer) -> _Attributes:
    sanitized = sanitizer.shovel(value)
    if sanitized is None:
        return (("value", None), *((name, None) for name in _SHOVEL_SETTING_TYPES))

    endpoints = {key: item for key, item in sanitized.items() if key not in _SHOVEL_SETTING_TYPES}
    settings: list[tuple[str, str | None]] = []
    for name in _SHOVEL_SETTING_TYPES:
        if name not in sanitized:
            settings.append((name, "{}"))
        elif sanitizer.has_opaque_values(sanitized[name]):
            settings.append((name, None))
        else:
            settings.append((name, json.dumps({name: sanitized[name]}, sort_keys=True)))
    return (("value", json.dumps(endpoints, sort_keys=True)), *settings)


def _indexed[T](rows: Iterable[tuple[T, _Attributes]]) -> dict[T, set[_Attributes]]:
    result: dict[T, set[_Attributes]] = {}
    for identity, attributes in rows:
        result.setdefault(identity, set()).add(attributes)
    return result


def _compare_declarations[T: (str, NodeId, PolicyId)](
    before: Iterable[tuple[T, _Attributes]],
    after: Iterable[tuple[T, _Attributes]],
    key: Callable[[T], str | tuple[str, str] | NodeSortKey],
    evidence: ComparisonEvidence,
) -> DeclarationComparison[T]:
    if any(gap != ComparisonGap.OUTSIDE_SHARED_SCOPE for gap in (*evidence.before, *evidence.after)):
        return DeclarationComparison(evidence=evidence)

    old: dict[T, set[_Attributes]] = _indexed(before)
    new: dict[T, set[_Attributes]] = _indexed(after)
    changes = []
    for identity in sorted(old.keys() & new.keys(), key=key):
        if len(old[identity]) != 1 or len(new[identity]) != 1:
            continue
        changed = []
        unavailable = []
        for (name, left), (_, right) in zip(next(iter(old[identity])), next(iter(new[identity])), strict=True):
            if left is None or right is None:
                unavailable.append(name)
            elif left != right:
                changed.append(name)
        if changed or unavailable:
            changes.append(DeclarationChange(identity, tuple(changed), tuple(unavailable)))

    return DeclarationComparison(
        added=tuple(sorted(new.keys() - old.keys(), key=key)),
        removed=tuple(sorted(old.keys() - new.keys(), key=key)),
        attributes=tuple(changes),
        evidence=ComparisonEvidence(
            (*evidence.before, *_duplicate_gap(old.values())),
            (*evidence.after, *_duplicate_gap(new.values())),
        ),
    )


def _duplicate_gap(rows: Iterable[set[_Attributes]]) -> tuple[ComparisonGap, ...]:
    return (ComparisonGap.AMBIGUOUS_DECLARATION,) if any(len(values) > 1 for values in rows) else ()


def _binding_group(edge: TopologyEdge) -> tuple[NodeId, NodeId, str | None]:
    return edge.source, edge.target, edge.routing_key


def _compare_bindings(
    before: _Input, after: _Input, cluster_id: str, scope: _Vhosts, scope_gaps: tuple[ComparisonGap, ...]
) -> BindingComparison:
    old_gaps, new_gaps = before.gaps("bindings"), after.gaps("bindings")
    if old_gaps or new_gaps:
        return BindingComparison(evidence=ComparisonEvidence(scope_gaps + old_gaps, scope_gaps + new_gaps))
    old, old_unknown = before.binding_rows(cluster_id, scope)
    new, new_unknown = after.binding_rows(cluster_id, scope)
    unknown = {_binding_group(edge) for edge in old_unknown | new_unknown}
    return BindingComparison(
        added=tuple(sorted((edge for edge in new - old if _binding_group(edge) not in unknown), key=edge_sort_key)),
        removed=tuple(sorted((edge for edge in old - new if _binding_group(edge) not in unknown), key=edge_sort_key)),
        evidence=ComparisonEvidence(
            scope_gaps + ((ComparisonGap.BINDING_ARGUMENTS_UNAVAILABLE,) if old_unknown else ()),
            scope_gaps + ((ComparisonGap.BINDING_ARGUMENTS_UNAVAILABLE,) if new_unknown else ()),
        ),
    )


def _compare_policy_selections(
    before: _Input, after: _Input, cluster_id: str, scope: _Vhosts, scope_gaps: tuple[ComparisonGap, ...]
) -> PolicySelectionComparison:
    def selections(capture: _Input) -> dict[tuple[NodeId, PolicyKind], PolicySelection]:
        result = {}
        for row in capture.auditor.policy_inventory().selections:
            if _in_scope(row.resource.vhost, scope):
                resource = replace(row.resource, cluster_id=cluster_id)
                result[resource, row.kind] = replace(row, resource=resource)
        return result

    old, new = selections(before), selections(after)
    changed, unavailable = [], []
    for key in sorted(old.keys() & new.keys(), key=lambda key: (node_sort_key(key[0]), key[1] is PolicyKind.OPERATOR)):
        left, right = old[key], new[key]
        pair = PolicySelectionChange(left, right)
        if PolicySelectionStatus.NOT_CAPTURED in (left.status, right.status):
            unavailable.append(pair)
        elif left != right:
            changed.append(pair)
    return PolicySelectionComparison(
        tuple(changed),
        tuple(unavailable),
        ComparisonEvidence(scope_gaps + before.observation_gaps(), scope_gaps + after.observation_gaps()),
    )


def _map_contract(contract: RoutingContract, source_id: str | None, target_id: str | None) -> RoutingContract:
    def map_resource(resource: NodeId) -> NodeId:
        if resource.cluster_id == source_id or (source_id is None and resource.cluster_id == ""):
            return replace(resource, cluster_id=target_id)
        return resource

    return replace(
        contract,
        origin=map_resource(contract.origin),
        conditions=tuple(
            replace(condition, queues=frozenset(map_resource(queue) for queue in condition.queues))
            for condition in contract.conditions
        ),
    )


def _routing_outcomes(
    capture: _Input, contracts: tuple[RoutingContract, ...], cluster_id: str
) -> tuple[RoutingContractOutcome, ...]:
    if not contracts:
        return ()
    auditor = capture.auditor
    resource_gaps = capture.gaps("queues", "exchanges")
    binding_gaps = capture.gaps("bindings") if capture.definitions is not None else ()
    known, unknown = capture.binding_rows(auditor.cluster_id, capture.vhosts)
    gaps = []
    for contract in contracts:
        vhost = contract.origin.vhost
        contract_gaps = resource_gaps
        if not _in_scope(vhost, capture.vhosts):
            contract_gaps += (ComparisonGap.OUTSIDE_SHARED_SCOPE,)
        if contract.origin.name != "":
            contract_gaps += binding_gaps
        gaps.append(contract_gaps)

    evaluated = iter(
        evaluate_routing_contracts(
            auditor.topology,
            tuple(
                _map_contract(contract, cluster_id, auditor.cluster_id)
                for contract, missing in zip(contracts, gaps, strict=True)
                if not missing
            ),
            incomplete_binding_arguments=frozenset(unknown - known),
        )
    )
    outcomes = []
    for contract, missing in zip(contracts, gaps, strict=True):
        if missing:
            outcomes.append(RoutingContractOutcome(gaps=missing))
            continue
        result = next(evaluated)
        outcomes.append(
            RoutingContractOutcome(
                replace(
                    result,
                    contract=contract,
                    conditions=tuple(
                        replace(row, condition=condition)
                        for row, condition in zip(result.conditions, contract.conditions, strict=True)
                    ),
                    established_destinations=tuple(
                        replace(node, cluster_id=cluster_id) for node in result.established_destinations
                    ),
                    conditional_destinations=tuple(
                        replace(node, cluster_id=cluster_id) for node in result.conditional_destinations
                    ),
                    issues=tuple(
                        replace(issue, resource=replace(issue.resource, cluster_id=cluster_id))
                        for issue in result.issues
                    ),
                )
            )
        )
    return tuple(outcomes)


def compare_captures(
    before: ClusterAuditor | AuditCapture,
    after: ClusterAuditor | AuditCapture,
    *,
    cluster_id: str | None = None,
    before_vhosts: Collection[str] | None = None,
    after_vhosts: Collection[str] | None = None,
    routing_contracts: Sequence[RoutingContract] = (),
) -> CaptureComparison:
    """Compare declarations, bindings, policy selections, and routing offline.

    Args:
        before: Baseline audit input.
        after: Later audit input.
        cluster_id: Explicit shared identity for inputs missing a broker ID.
            It must agree with any available broker ID.
        before_vhosts: Complete vhost scope of the baseline if it is a projection.
        after_vhosts: Complete vhost scope of the later input if it is a projection.
        routing_contracts: Publication conditions to evaluate against both inputs.

    Returns:
        Confirmed deltas within the shared scope and any evidence gaps.

    Raises:
        TopologyAnalysisError: If identity is conflicting or unresolved, or a
            comparison argument is invalid.
    """
    baseline, later = _input(before, before_vhosts), _input(after, after_vhosts)
    identity = _cluster_id(baseline, later, cluster_id)
    _validate_contracts(routing_contracts)
    if any(contract.origin.cluster_id not in {None, "", identity} for contract in routing_contracts):
        raise TopologyAnalysisError("Routing contract origins must belong to the compared cluster")
    contracts = tuple(_map_contract(contract, None, identity) for contract in routing_contracts)
    if baseline.vhosts is None:
        scope = later.vhosts
    elif later.vhosts is None:
        scope = baseline.vhosts
    else:
        scope = baseline.vhosts & later.vhosts
    scope_gaps = () if baseline.vhosts == later.vhosts else (ComparisonGap.OUTSIDE_SHARED_SCOPE,)
    return _compare(baseline, later, identity, scope, scope_gaps, contracts)


def _compare(
    before: _Input,
    after: _Input,
    cluster_id: str,
    scope: _Vhosts,
    scope_gaps: tuple[ComparisonGap, ...],
    contracts: tuple[RoutingContract, ...],
) -> CaptureComparison:
    def evidence(section: str) -> ComparisonEvidence:
        return ComparisonEvidence(scope_gaps + before.gaps(section), scope_gaps + after.gaps(section))

    sanitizer = CaptureSanitizer()

    def vhosts(capture: _Input) -> Iterable[tuple[str, _Attributes]]:
        if capture.definitions is not None:
            for row in capture.definitions.vhosts:
                if _in_scope(row.name, scope):
                    values = row.metadata.model_dump(exclude_unset=True) if row.metadata is not None else {}
                    yield row.name, _attribute_values(values, ("default_queue_type",), sanitizer)

    def nodes(capture: _Input, kind: NodeKind) -> Iterable[tuple[NodeId, _Attributes]]:
        definitions = capture.definitions
        if definitions is None:
            return
        if kind is NodeKind.SHOVEL:
            for parameter in definitions.parameters:
                if parameter.component == "shovel" and _in_scope(parameter.vhost, scope):
                    attributes = _shovel_attributes(parameter.value, sanitizer)
                    yield NodeId(parameter.vhost, parameter.name, kind, cluster_id), attributes
            return
        rows = definitions.queues if kind is NodeKind.QUEUE else definitions.exchanges
        fields: tuple[str, ...] = ("arguments", "auto_delete", "durable")
        if kind is NodeKind.EXCHANGE:
            fields += ("internal", "type")
        for row in rows:
            if _in_scope(row.vhost, scope):
                yield (
                    NodeId(row.vhost, row.name, kind, cluster_id),
                    _attribute_values(row.model_dump(by_alias=True, exclude_unset=True), fields, sanitizer),
                )

    def policies(capture: _Input, kind: PolicyKind) -> Iterable[tuple[PolicyId, _Attributes]]:
        definitions = capture.definitions
        if definitions is None:
            return
        if kind is PolicyKind.REGULAR:
            for policy in definitions.policies:
                if _in_scope(policy.vhost, scope):
                    attributes = _attribute_values(
                        policy.model_dump(by_alias=True, exclude_unset=True),
                        ("apply-to", "definition", "pattern", "priority"),
                        sanitizer,
                    )
                    yield PolicyId(policy.vhost, policy.name, kind, cluster_id), attributes
        else:
            for parameter in definitions.parameters:
                if parameter.component == "operator_policy" and _in_scope(parameter.vhost, scope):
                    attributes = _attribute_values({"value": parameter.value}, ("value",), sanitizer)
                    yield PolicyId(parameter.vhost, parameter.name, kind, cluster_id), attributes

    return CaptureComparison(
        cluster_id=cluster_id,
        before_vhosts=tuple(sorted(before.vhosts)) if before.vhosts is not None else None,
        after_vhosts=tuple(sorted(after.vhosts)) if after.vhosts is not None else None,
        vhosts=_compare_declarations(vhosts(before), vhosts(after), str, evidence("vhosts")),
        queues=_compare_declarations(
            nodes(before, NodeKind.QUEUE), nodes(after, NodeKind.QUEUE), node_sort_key, evidence("queues")
        ),
        exchanges=_compare_declarations(
            nodes(before, NodeKind.EXCHANGE), nodes(after, NodeKind.EXCHANGE), node_sort_key, evidence("exchanges")
        ),
        shovels=_compare_declarations(
            nodes(before, NodeKind.SHOVEL), nodes(after, NodeKind.SHOVEL), node_sort_key, evidence("parameters")
        ),
        regular_policies=_compare_declarations(
            policies(before, PolicyKind.REGULAR),
            policies(after, PolicyKind.REGULAR),
            lambda policy: (policy.vhost, policy.name),
            evidence("policies"),
        ),
        operator_policies=_compare_declarations(
            policies(before, PolicyKind.OPERATOR),
            policies(after, PolicyKind.OPERATOR),
            lambda policy: (policy.vhost, policy.name),
            evidence("parameters"),
        ),
        bindings=_compare_bindings(before, after, cluster_id, scope, scope_gaps),
        policy_selections=_compare_policy_selections(before, after, cluster_id, scope, scope_gaps),
        routing_contracts=tuple(
            RoutingContractComparison(contract, left, right)
            for contract, left, right in zip(
                contracts,
                _routing_outcomes(before, contracts, cluster_id),
                _routing_outcomes(after, contracts, cluster_id),
                strict=True,
            )
        ),
    )
