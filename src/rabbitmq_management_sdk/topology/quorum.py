"""Expose quorum delivery evidence without changing structural routing."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from rabbitmq_management_sdk.exceptions import TopologyValidationError
from rabbitmq_management_sdk.topology.models import NodeId, NodeKind, QuorumDeliveryConfiguration
from rabbitmq_management_sdk.topology.ordering import node_sort_key

if TYPE_CHECKING:
    from rabbitmq_management_sdk.topology.models import ClusterTopology


class QuorumConfigurationIssue(StrEnum):
    """Configuration facts for caller interpretation, without severity ranking."""

    RETRY_DISABLED_WITHOUT_MINIMUM = "retry_disabled_without_minimum"
    AT_LEAST_ONCE_FALLBACK = "at_least_once_fallback"
    FINITE_DELIVERY_LIMIT_WITHOUT_DLX = "finite_delivery_limit_without_dlx"


@dataclass(frozen=True, slots=True)
class QuorumQueueConfiguration:
    """Delivery configuration and advisory facts for one declared quorum queue."""

    queue: NodeId
    configuration: QuorumDeliveryConfiguration
    issues: tuple[QuorumConfigurationIssue, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.queue, NodeId) or self.queue.kind != NodeKind.QUEUE:
            raise TopologyValidationError("QuorumQueueConfiguration.queue must be a queue NodeId")
        if not isinstance(self.configuration, QuorumDeliveryConfiguration):
            raise TopologyValidationError("QuorumQueueConfiguration.configuration must be QuorumDeliveryConfiguration")
        if not isinstance(self.issues, tuple) or any(
            not isinstance(item, QuorumConfigurationIssue) for item in self.issues
        ):
            raise TopologyValidationError("QuorumQueueConfiguration.issues must be a tuple of QuorumConfigurationIssue")
        object.__setattr__(self, "issues", tuple(sorted(set(self.issues), key=lambda item: item.value)))


def quorum_delivery_configuration(topology: ClusterTopology) -> tuple[QuorumQueueConfiguration, ...]:
    """Return every contained quorum queue, retaining unknown configuration."""
    results = []
    for queue in sorted(topology.queues, key=lambda queue: node_sort_key(queue.id)):
        if queue.queue_type != "quorum":
            continue
        config = queue.quorum_delivery or QuorumDeliveryConfiguration()
        issues = []
        if (
            config.configured_retry_type.value in {"all", "failed", "returned"}
            and config.delayed_retry_type.value == "disabled"
        ):
            issues.append(QuorumConfigurationIssue.RETRY_DISABLED_WITHOUT_MINIMUM)
        if (
            config.dead_letter_strategy.value == "at-least-once"
            and config.effective_dead_letter_strategy.value == "at-most-once"
        ):
            issues.append(QuorumConfigurationIssue.AT_LEAST_ONCE_FALLBACK)
        if (
            config.delivery_limit.value is not None
            and config.delivery_limit.value >= 0
            and config.effective_dead_letter_strategy.value == "disabled"
        ):
            issues.append(QuorumConfigurationIssue.FINITE_DELIVERY_LIMIT_WITHOUT_DLX)
        results.append(QuorumQueueConfiguration(queue.id, config, tuple(issues)))
    return tuple(results)
