"""Adapters from Management API resource models to topology policy evidence."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING

from rabbitmq_management_sdk.exceptions import TopologyValidationError
from rabbitmq_management_sdk.topology.models import NodeId, NodeKind

if TYPE_CHECKING:
    from collections.abc import Iterable

    from rabbitmq_management_sdk.resources.v4.exchanges.schemas.exchange_response import ExchangeResponse
    from rabbitmq_management_sdk.resources.v4.queues.schemas.queue_response import QueueResponse
    from rabbitmq_management_sdk.topology.policy_routes import UserPolicySelections


def build_user_policy_selections(
    *,
    queues: Iterable[QueueResponse],
    exchanges: Iterable[ExchangeResponse],
    cluster_id: str | None,
) -> UserPolicySelections:
    """Normalize observed queue and exchange policies for topology analysis.

    The caller decides how the responses were obtained: a complete saved dump,
    paginated API responses, or another source. Runtime statistics and all
    response data other than resource identity and the selected regular policy
    are intentionally excluded from the returned immutable mapping.

    This is an implementation adapter for :class:`ClusterAuditor`; callers
    provide the normalized resource records to that facade instead.
    """
    selections: dict[NodeId, str | None] = {}

    def add(resource: NodeId, policy_name: str | None) -> None:
        if resource in selections:
            raise TopologyValidationError(
                f"Duplicate user policy selections for {resource!r}: {selections[resource]!r} and {policy_name!r}"
            )
        if policy_name is not None and not policy_name:
            raise TopologyValidationError("Observed user policy names must be non-empty")
        selections[resource] = policy_name

    for queue in queues:
        add(
            NodeId(
                cluster_id=cluster_id,
                vhost=queue.vhost,
                name=queue.name,
                kind=NodeKind.QUEUE,
            ),
            queue.policy,
        )
    for exchange in exchanges:
        add(
            NodeId(
                cluster_id=cluster_id,
                vhost=exchange.vhost,
                name=exchange.name,
                kind=NodeKind.EXCHANGE,
            ),
            exchange.policy,
        )
    return MappingProxyType(selections)
