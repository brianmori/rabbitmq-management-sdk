from typing import Annotated, Literal

from pydantic import Field

from rabbitmq_management_sdk.domains.base import RabbitMQBase

# ---------------------------------------------------------------------------
# Arguments — per queue type (response side)
# ---------------------------------------------------------------------------


class ClassicQueueResponse(RabbitMQBase):
    """Arguments echoed back by the server for a classic queue."""

    queue_type: Literal["classic"] = Field(alias="x-queue-type")
    message_ttl: int | None = Field(None, alias="x-message-ttl")
    max_length: int | None = Field(None, alias="x-max-length")
    max_length_bytes: int | None = Field(None, alias="x-max-length-bytes")
    overflow: str | None = Field(None, alias="x-overflow")
    dead_letter_exchange: str | None = Field(None, alias="x-dead-letter-exchange")
    dead_letter_routing_key: str | None = Field(None, alias="x-dead-letter-routing-key")
    single_active_consumer: bool | None = Field(None, alias="x-single-active-consumer")
    expires: int | None = Field(None, alias="x-expires")


class QuorumQueueResponse(RabbitMQBase):
    """Arguments echoed back by the server for a quorum queue."""

    queue_type: Literal["quorum"] = Field(alias="x-queue-type")
    delivery_limit: int | None = Field(None, alias="x-delivery-limit")
    dead_letter_exchange: str | None = Field(None, alias="x-dead-letter-exchange")
    dead_letter_routing_key: str | None = Field(None, alias="x-dead-letter-routing-key")
    dead_letter_strategy: str | None = Field(None, alias="x-dead-letter-strategy")
    single_active_consumer: bool | None = Field(None, alias="x-single-active-consumer")
    max_length: int | None = Field(None, alias="x-max-length")
    initial_cluster_size: int | None = Field(None, alias="x-initial-cluster-size")


class StreamQueueResponse(RabbitMQBase):
    """Arguments echoed back by the server for a stream queue."""

    queue_type: Literal["stream"] = Field(alias="x-queue-type")
    max_length_bytes: int | None = Field(None, alias="x-max-length-bytes")
    max_age: str | None = Field(None, alias="x-max-age")
    stream_max_segment_size: int | None = Field(None, alias="x-stream-max-segment-size-bytes")
    initial_cluster_size: int | None = Field(None, alias="x-initial-cluster-size")


type QueueResponseArguments = Annotated[
    ClassicQueueResponse | QuorumQueueResponse | StreamQueueResponse,
    Field(discriminator="queue_type"),
]


# ---------------------------------------------------------------------------
# Stats — optional, may be stripped by the API
# ---------------------------------------------------------------------------


class MessageRate(RabbitMQBase):
    """Rate detail attached to a message stats counter."""

    rate: float


class MessageStats(RabbitMQBase):
    """Live message throughput counters.

    Present only when stats are not stripped from the response. Individual
    counters may be absent depending on queue activity.
    """

    publish: int | None = None
    publish_details: MessageRate | None = None
    deliver_get: int | None = None
    deliver_get_details: MessageRate | None = None
    deliver: int | None = None
    deliver_details: MessageRate | None = None
    deliver_no_ack: int | None = None
    deliver_no_ack_details: MessageRate | None = None
    get: int | None = None
    get_details: MessageRate | None = None
    ack: int | None = None
    ack_details: MessageRate | None = None
    redeliver: int | None = None
    redeliver_details: MessageRate | None = None


# ---------------------------------------------------------------------------
# Queue — v4 baseline response model
# ---------------------------------------------------------------------------


class Queue(RabbitMQBase):
    """Represents a queue as returned by the RabbitMQ Management API (v4+).

    Fields are required when the v4 API guarantees their presence. Stats
    fields are ``None`` when the API response omits them (e.g., when queried
    with ``?columns=`` or when the queue is newly created).

    Example::

        queue = Queue.model_validate(api_response)
        queue.name  # str — always present
        queue.type  # "classic" | "quorum" | "stream" — always present in v4
        queue.messages  # int | None — absent if stats were stripped
    """

    # -- Identity (always present) --
    name: str
    vhost: str
    durable: bool
    auto_delete: bool
    exclusive: bool
    type: Literal["classic", "quorum", "stream"]

    # -- Cluster placement (always present in v4) --
    node: str
    state: str

    # -- Arguments --
    arguments: QueueResponseArguments

    # -- Stats (optional — stripped under certain conditions) --
    consumers: int | None = None
    consumer_utilisation: float | None = None
    memory: int | None = None
    messages: int | None = None
    messages_ready: int | None = None
    messages_unacknowledged: int | None = None
    message_stats: MessageStats | None = None
