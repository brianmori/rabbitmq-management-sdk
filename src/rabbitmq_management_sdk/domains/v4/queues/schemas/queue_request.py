import re
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from rabbitmq_management_sdk.domains.base import RabbitMQBase

# ---------------------------------------------------------------------------
# Arguments — per queue type (request side)
# ---------------------------------------------------------------------------


class DeadLetterStrategy(StrEnum):
    AT_MOST_ONCE = "at-most-once"
    AT_LEAST_ONCE = "at-least-once"


class Overflow(StrEnum):
    DROP_HEAD = "drop-head"
    REJECT_PUBLISH = "reject-publish"


class ClassicQueueRequest(RabbitMQBase):
    """Arguments for declaring a classic queue.

    Attributes:
        queue_type: Fixed at "classic".
        message_ttl: Message expiration in milliseconds.
        max_length: Limit on the number of messages; triggers overflow policy.
        max_length_bytes: Limit on total queue size in bytes.
        overflow: Strategy ("drop-head", "reject-publish") when limits hit.
        dead_letter_exchange: Target exchange for expired/rejected messages.
        dead_letter_routing_key: Routing key for the dead letter exchange.
        single_active_consumer: If True, only one consumer receives messages.
        expires: TTL (ms) for the queue itself when unused.
    """

    queue_type: Literal["classic"] = Field("classic", alias="x-queue-type", frozen=True)
    message_ttl: int | None = Field(None, alias="x-message-ttl")
    max_length: int | None = Field(None, alias="x-max-length")
    max_length_bytes: int | None = Field(None, alias="x-max-length-bytes")
    overflow: Overflow | None = Field(Overflow.DROP_HEAD, alias="x-overflow")
    dead_letter_exchange: str | None = Field(None, alias="x-dead-letter-exchange")
    dead_letter_routing_key: str | None = Field(None, alias="x-dead-letter-routing-key")
    single_active_consumer: bool | None = Field(None, alias="x-single-active-consumer")
    expires: int | None = Field(None, alias="x-expires")


class QuorumQueueRequest(RabbitMQBase):
    """Arguments for declaring a high-availability quorum queue.

    Attributes:
        queue_type: Fixed at "quorum" for replicated consistency.
        delivery_limit: Max delivery attempts before dropping or dead-lettering.
        dead_letter_exchange: Exchange to route failed or expired messages.
        dead_letter_routing_key: Routing key used for the dead-letter exchange.
        dead_letter_strategy: Routing strategy ("at-most-once", "at-least-once").
        single_active_consumer: If True, restricts consumption to one node at a time.
        max_length: Limit on total message count before overflow logic triggers.
        initial_cluster_size: Number of nodes the queue should be hosted on.
    """

    queue_type: Literal["quorum"] = Field("quorum", alias="x-queue-type", frozen=True)
    delivery_limit: int | None = Field(None, alias="x-delivery-limit")
    dead_letter_exchange: str | None = Field(None, alias="x-dead-letter-exchange")
    dead_letter_routing_key: str | None = Field(None, alias="x-dead-letter-routing-key")
    dead_letter_strategy: DeadLetterStrategy | None = Field(
        DeadLetterStrategy.AT_MOST_ONCE, alias="x-dead-letter-strategy"
    )
    overflow: Overflow | None = Field(Overflow.DROP_HEAD, alias="x-overflow")
    single_active_consumer: bool | None = Field(None, alias="x-single-active-consumer")
    max_length: int | None = Field(None, alias="x-max-length")
    initial_cluster_size: int | None = Field(None, alias="x-initial-cluster-size")

    @model_validator(mode="after")
    def quorum_queue_validate(self) -> QuorumQueueRequest:
        """
        Ensures that 'at-least-once' dead lettering is only used
        with 'reject-publish' overflow.

        Raises:
            ValueError: If 'at-least-once' dead lettering is used with non-'reject-publish' overflow.
        """
        if self.dead_letter_strategy == DeadLetterStrategy.AT_LEAST_ONCE and self.overflow != Overflow.REJECT_PUBLISH:
            raise ValueError(
                f"dead_letter_strategy '{DeadLetterStrategy.AT_LEAST_ONCE}' "
                f"requires overflow to be set to '{Overflow.REJECT_PUBLISH}'."
            )
        return self


class StreamQueueRequest(RabbitMQBase):
    """Arguments for declaring a high-throughput Stream queue.

    Attributes:
        queue_type: Fixed at "stream" for append-only log storage.
        max_length_bytes: Maximum total size of the stream on disk.
        max_age: Maximum age of data (e.g., "7d", "24h", "30m").
            Older data is truncated.
        stream_max_segment_size: Size in bytes for individual segment files on disk.
        initial_cluster_size: Number of cluster nodes used for replication (must be odd).
    """

    queue_type: Literal["stream"] = Field("stream", alias="x-queue-type", frozen=True)
    max_length_bytes: int | None = Field(None, alias="x-max-length-bytes")
    max_age: str | None = Field(None, alias="x-max-age")
    stream_max_segment_size: int | None = Field(None, alias="x-stream-max-segment-size-bytes")
    initial_cluster_size: int | None = Field(None, alias="x-initial-cluster-size")

    @field_validator("max_age")
    @classmethod
    def validate_max_age_format(cls, v: str | None) -> str | None:
        """Validates that max_age follows the RabbitMQ time-unit format."""
        if v is None:
            return v

        # Pattern: one or more digits followed by exactly one unit (Y, M, D, d, h, m, s)
        if not re.match(r"^\d+[YMDdhms]$", v):
            raise ValueError(
                f"Invalid max_age format: '{v}'. Must be a number followed by a unit (e.g., '7d', '24h', '30m')."
            )
        return v


type QueueRequestArguments = Annotated[
    ClassicQueueRequest | QuorumQueueRequest | StreamQueueRequest,
    Field(discriminator="queue_type"),
]


class QueueDeleteOptions(RabbitMQBase):
    if_empty: bool = Field(False, alias="if-empty")
    if_unused: bool = Field(False, alias="if-unused")

    def to_query_params(self) -> dict[str, str]:
        params = {}
        if self.if_empty:
            params["if-empty"] = "true"
        if self.if_unused:
            params["if-unused"] = "true"
        return params


# ---------------------------------------------------------------------------
# Queue request payload
# ---------------------------------------------------------------------------


class QueueRequest(RabbitMQBase):
    """Full payload for ``HTTP_METHOD /api/queues/{vhost}/{name}``.

    Example — classic queue with TTL::

        QueueRequest(
            arguments=ClassicQueueRequest(message_ttl=60000),
        )

    Example — quorum queue::

        QueueRequest(
            arguments=QuorumQueueRequest(delivery_limit=5),
        )
    """

    durable: bool = True
    auto_delete: bool = False
    arguments: QueueRequestArguments
