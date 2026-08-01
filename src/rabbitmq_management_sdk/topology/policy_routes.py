"""Resolve policy-derived routing settings without evaluating policy regexes.

RabbitMQ evaluates policy patterns using its Erlang regular-expression engine.
This module therefore treats an observed Management API policy selection as the
authority for which regular user policy the broker selected at capture time.
It checks that selection against the definitions export's vhost and
``apply-to`` scope, then resolves topology-relevant settings. These settings
are configuration evidence; they do not prove runtime delivery.
"""

from collections.abc import Mapping

from rabbitmq_management_sdk.exceptions import TopologyParseError
from rabbitmq_management_sdk.resources.v4.admin.schemas.export_response import (
    DefinitionPolicy,
    PolicyApplyTo,
)
from rabbitmq_management_sdk.topology.models import NodeId, NodeKind

type UserPolicySelections = Mapping[NodeId, str | None]
"""Observed policy name by resource; a present ``None`` means no policy."""


def _apply_to_matches(apply_to: PolicyApplyTo, kind: NodeKind, queue_type: str | None) -> bool:
    """Return whether a policy's ``apply-to`` scope covers one resource."""
    if kind == NodeKind.SHOVEL:
        return False
    if apply_to == PolicyApplyTo.ALL:
        return True
    if kind == NodeKind.EXCHANGE:
        return apply_to == PolicyApplyTo.EXCHANGES
    if kind == NodeKind.QUEUE:
        return apply_to == PolicyApplyTo.QUEUES or (
            queue_type is not None
            and {
                "classic": PolicyApplyTo.CLASSIC_QUEUES,
                "quorum": PolicyApplyTo.QUORUM_QUEUES,
                "stream": PolicyApplyTo.STREAMS,
            }.get(queue_type)
            == apply_to
        )
    return False


def _observed_user_policy(
    *,
    node_id: NodeId,
    queue_type: str | None,
    policies: list[DefinitionPolicy],
    user_policy_selections: UserPolicySelections,
) -> tuple[bool, DefinitionPolicy | None]:
    """Return observed regular-policy evidence without evaluating a pattern."""
    if node_id not in user_policy_selections:
        return False, None
    policy_name = user_policy_selections[node_id]
    if policy_name is None:
        return True, None

    selected = [policy for policy in policies if policy.vhost == node_id.vhost and policy.name == policy_name]
    if len(selected) != 1:
        raise TopologyParseError(
            f"User-policy selection {policy_name!r} for "
            f"{node_id.kind.value} {node_id.vhost!r}/{node_id.name!r} "
            "does not identify exactly one policy in the definitions export"
        )
    policy = selected[0]
    if not _apply_to_matches(policy.apply_to, node_id.kind, queue_type):
        raise TopologyParseError(
            f"User-policy selection {policy_name!r} for "
            f"{node_id.kind.value} {node_id.vhost!r}/{node_id.name!r} "
            f"has incompatible apply-to value {policy.apply_to!r}"
        )
    return True, policy


def _policies_that_can_set(
    *,
    vhost: str,
    kind: NodeKind,
    queue_type: str | None,
    policies: list[DefinitionPolicy],
    definition_field: str,
) -> tuple[DefinitionPolicy, ...]:
    """Return policies that could set one topology-relevant definition key.

    Without an observed broker selection, compatible vhost and ``apply-to``
    scope show only that a policy might apply. The caller must require that
    selection instead of approximating Erlang's matching semantics with
    Python's regular-expression engine.
    """
    return tuple(
        policy
        for policy in policies
        if policy.vhost == vhost
        and _apply_to_matches(policy.apply_to, kind, queue_type)
        and getattr(policy.definition, definition_field) is not None
    )


def _require_user_policy_selection(
    *,
    vhost: str,
    name: str,
    kind: NodeKind,
    route_setting: str,
    policies: tuple[DefinitionPolicy, ...],
) -> None:
    """Raise a precise error when policy pattern evaluation would be required."""
    names = ", ".join(sorted(policy.name for policy in policies))
    raise TopologyParseError(
        f"Cannot determine {route_setting} for {kind.value} {vhost!r}/{name!r}: "
        f"routing-relevant user policies may apply ({names}). Supply observed QueueResponse and "
        "ExchangeResponse records to ClusterAuditor so it can use the broker-selected policy for this resource."
    )


def resolve_dead_letter_values(
    *,
    queue_id: NodeId,
    queue_type: str | None,
    declared_exchange: str | None,
    declared_routing_key: str | None,
    policies: list[DefinitionPolicy],
    user_policy_selections: UserPolicySelections,
) -> tuple[str | None, str | None]:
    """Resolve dead-letter settings from direct arguments and policy evidence.

    A direct queue argument takes precedence for its individual setting. An
    observed, selected regular user policy supplies any missing setting.
    """
    observed, selected_policy = _observed_user_policy(
        node_id=queue_id,
        queue_type=queue_type,
        policies=policies,
        user_policy_selections=user_policy_selections,
    )
    if observed:
        return (
            declared_exchange
            if declared_exchange is not None
            else (selected_policy.definition.dead_letter_exchange if selected_policy is not None else None),
            declared_routing_key
            if declared_routing_key is not None
            else (selected_policy.definition.dead_letter_routing_key if selected_policy is not None else None),
        )

    if declared_exchange is None:
        policies_with_dlx = _policies_that_can_set(
            vhost=queue_id.vhost,
            kind=NodeKind.QUEUE,
            queue_type=queue_type,
            policies=policies,
            definition_field="dead_letter_exchange",
        )
        if policies_with_dlx:
            _require_user_policy_selection(
                vhost=queue_id.vhost,
                name=queue_id.name,
                kind=queue_id.kind,
                route_setting="dead-letter exchange",
                policies=policies_with_dlx,
            )
        return None, declared_routing_key

    if declared_routing_key is None:
        policies_with_routing_key = _policies_that_can_set(
            vhost=queue_id.vhost,
            kind=NodeKind.QUEUE,
            queue_type=queue_type,
            policies=policies,
            definition_field="dead_letter_routing_key",
        )
        if policies_with_routing_key:
            _require_user_policy_selection(
                vhost=queue_id.vhost,
                name=queue_id.name,
                kind=queue_id.kind,
                route_setting="dead-letter routing key",
                policies=policies_with_routing_key,
            )
    return declared_exchange, declared_routing_key


def resolve_alternate_exchange(
    *,
    exchange_id: NodeId,
    declared_alternate_exchange: str | None,
    policies: list[DefinitionPolicy],
    user_policy_selections: UserPolicySelections,
) -> str | None:
    """Resolve an alternate exchange from direct arguments and policy evidence.

    A direct exchange argument takes precedence. Otherwise, an observed,
    selected regular user policy supplies the setting.
    """
    observed, selected_policy = _observed_user_policy(
        node_id=exchange_id,
        queue_type=None,
        policies=policies,
        user_policy_selections=user_policy_selections,
    )
    if observed:
        return (
            declared_alternate_exchange
            if declared_alternate_exchange is not None
            else (selected_policy.definition.alternate_exchange if selected_policy is not None else None)
        )

    if declared_alternate_exchange is None:
        policies_with_ae = _policies_that_can_set(
            vhost=exchange_id.vhost,
            kind=NodeKind.EXCHANGE,
            queue_type=None,
            policies=policies,
            definition_field="alternate_exchange",
        )
        if policies_with_ae:
            _require_user_policy_selection(
                vhost=exchange_id.vhost,
                name=exchange_id.name,
                kind=exchange_id.kind,
                route_setting="alternate exchange",
                policies=policies_with_ae,
            )
    return declared_alternate_exchange
