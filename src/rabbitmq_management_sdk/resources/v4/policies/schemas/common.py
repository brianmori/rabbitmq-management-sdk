from __future__ import annotations

from enum import StrEnum


class PolicyApplyTo(StrEnum):
    """Resource scopes accepted by regular RabbitMQ policies."""

    QUEUES = "queues"
    CLASSIC_QUEUES = "classic_queues"
    QUORUM_QUEUES = "quorum_queues"
    STREAMS = "streams"
    EXCHANGES = "exchanges"
    ALL = "all"


class OperatorPolicyApplyTo(StrEnum):
    """Resource scopes accepted by RabbitMQ operator policies."""

    QUEUES = "queues"
    CLASSIC_QUEUES = "classic_queues"
    QUORUM_QUEUES = "quorum_queues"
    STREAMS = "streams"
