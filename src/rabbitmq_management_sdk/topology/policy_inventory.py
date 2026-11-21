"""Build a reverse index from explicit policy selections in captured observations."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import TYPE_CHECKING

from pydantic import ValidationError

from rabbitmq_management_sdk.topology.models import (
    NodeId,
    NodeKind,
    PolicyCoverage,
    PolicyDeclaration,
    PolicyId,
    PolicyInventory,
    PolicyKind,
    PolicySelection,
    PolicySelectionStatus,
)
from rabbitmq_management_sdk.topology.ordering import node_sort_key, optional_string_sort_key
from rabbitmq_management_sdk.topology.policy_routes import _apply_to_matches

if TYPE_CHECKING:
    from collections.abc import Collection

    from rabbitmq_management_sdk.resources.v4.admin.schemas.export_response import ClusterDefinitionsResponse
    from rabbitmq_management_sdk.resources.v4.exchanges.schemas.exchange_response import ExchangeResponse
    from rabbitmq_management_sdk.resources.v4.queues.schemas.queue_response import QueueResponse
    from rabbitmq_management_sdk.topology.models import ClusterTopology, ExchangeNode, QueueNode


_SELECTION_FIELDS = ((PolicyKind.REGULAR, "policy"), (PolicyKind.OPERATOR, "operator_policy"))


def _selection(
    resource: NodeId, kind: PolicyKind, field: str, observation: QueueResponse | ExchangeResponse | None
) -> PolicySelection:
    if observation is None or field not in observation.model_fields_set:
        return PolicySelection(resource, kind)
    name = observation.policy if kind is PolicyKind.REGULAR else observation.operator_policy
    if name is None:
        return PolicySelection(resource, kind, PolicySelectionStatus.NO_SELECTION)
    if not name:
        return PolicySelection(resource, kind)
    return PolicySelection(resource, kind, PolicySelectionStatus.SELECTED, name)


def _selections(
    resources: Collection[NodeId], observations: dict[NodeId, QueueResponse | ExchangeResponse]
) -> tuple[PolicySelection, ...]:
    return tuple(
        _selection(resource, kind, field, observations.get(resource))
        for resource in sorted(resources, key=node_sort_key)
        for kind, field in _SELECTION_FIELDS
    )


def _declarations(
    definitions: ClusterDefinitionsResponse, cluster_id: str | None
) -> tuple[list[PolicyDeclaration], set[PolicyId]]:
    declarations = [
        PolicyDeclaration(
            PolicyId(policy.vhost, policy.name, PolicyKind.REGULAR, cluster_id),
            policy.pattern,
            policy.apply_to,
            policy.priority,
        )
        for policy in definitions.policies
    ]
    unreadable: set[PolicyId] = set()
    for parameter in definitions.parameters:
        if parameter.component != "operator_policy":
            continue
        policy_id = PolicyId(parameter.vhost, parameter.name, PolicyKind.OPERATOR, cluster_id)
        try:
            operator = parameter.as_operator_policy()
        except ValidationError:
            unreadable.add(policy_id)
            continue
        if operator is not None:
            declarations.append(PolicyDeclaration(policy_id, operator.pattern, operator.apply_to, operator.priority))
    return declarations, unreadable


def _declaration_sort_key(declaration: PolicyDeclaration) -> tuple[tuple[int, str], str, str, str, str, str, int]:
    policy = declaration.id
    return (
        optional_string_sort_key(policy.cluster_id),
        policy.vhost,
        policy.kind.value,
        policy.name,
        declaration.pattern,
        declaration.apply_to,
        declaration.priority,
    )


def capture_policy_inventory(
    definitions: ClusterDefinitionsResponse,
    *,
    cluster_id: str | None,
    queues: frozenset[QueueNode],
    exchanges: frozenset[ExchangeNode],
    observed_queues: tuple[QueueResponse, ...],
    observed_exchanges: tuple[ExchangeResponse, ...],
) -> PolicyInventory:
    """Project the declaration headers and named, null or missing selection evidence."""
    resource_types: dict[NodeId, str | None] = {queue.id: queue.queue_type for queue in queues}
    resource_types.update((exchange.id, None) for exchange in exchanges)
    observations: dict[NodeId, QueueResponse | ExchangeResponse] = {}
    for kind, observed in ((NodeKind.QUEUE, observed_queues), (NodeKind.EXCHANGE, observed_exchanges)):
        for observation in observed:
            resource = NodeId(observation.vhost, observation.name, kind, cluster_id)
            observations[resource] = observation
            resource_types.setdefault(resource, observation.type if kind == NodeKind.QUEUE else None)
    selections = _selections(resource_types.keys(), observations)

    declarations, unreadable = _declarations(definitions, cluster_id)
    counts = Counter(declaration.id for declaration in declarations)
    compatible_scopes = {
        item.id: item.apply_to for item in declarations if counts[item.id] == 1 and item.id not in unreadable
    }
    selected: dict[PolicyId, list[NodeId]] = defaultdict(list)
    for selection in selections:
        policy_id = selection.policy_id
        if policy_id is not None and (scope := compatible_scopes.get(policy_id)) is not None:
            resource = selection.resource
            if _apply_to_matches(scope, resource.kind, resource_types[resource]):
                selected[policy_id].append(resource)

    return PolicyInventory(
        tuple(
            PolicyCoverage(item, tuple(selected.get(item.id, ())))
            for item in sorted(declarations, key=_declaration_sort_key)
        ),
        selections,
    )


def policy_inventory(topology: ClusterTopology) -> PolicyInventory:
    """Read the captured inventory or supply missing rows for a hand-built graph."""
    captured = topology.policy_inventory
    if captured.selections or captured.policies:
        return captured
    resources = {queue.id for queue in topology.queues} | {exchange.id for exchange in topology.exchanges}
    return PolicyInventory(selections=_selections(resources, {}))
