from __future__ import annotations

from enum import StrEnum
from typing import Literal


class DelayedRetryType(StrEnum):
    """Quorum queue retry selectors supported starting with RabbitMQ 4.3."""

    DISABLED = "disabled"
    ALL = "all"
    FAILED = "failed"
    RETURNED = "returned"


class DeadLetterStrategy(StrEnum):
    """Dead-lettering strategies used by queue and policy wire models."""

    AT_MOST_ONCE = "at-most-once"
    AT_LEAST_ONCE = "at-least-once"


class OverflowBehaviour(StrEnum):
    """Overflow behaviours observed in RabbitMQ definitions and policies."""

    DROP_HEAD = "drop-head"
    REJECT_PUBLISH = "reject-publish"
    REJECT_PUBLISH_DLX = "reject-publish-dlx"


class QueueLeaderLocator(StrEnum):
    """Queue leader placement values used by arguments and policies."""

    CLIENT_LOCAL = "client-local"
    BALANCED = "balanced"


# Preserve deprecated values still returned by the broker. Queue declarations
# accept one additional legacy value beyond regular policy definitions.
type CapturedPolicyLeaderLocator = QueueLeaderLocator | Literal["random", "least-leaders"]
type CapturedQueueLeaderLocator = CapturedPolicyLeaderLocator | Literal["min-masters"]
