from __future__ import annotations

from pydantic import Field

from rabbitmq_management_sdk.resources.base import RabbitMQBase
from rabbitmq_management_sdk.resources.v4.common import (
    DeadLetterStrategy,
    OverflowBehaviour,
    QueueLeaderLocator,
)
from rabbitmq_management_sdk.resources.v4.policies.schemas.common import (
    OperatorPolicyApplyTo,
    PolicyApplyTo,
)


class PolicyDefinitionResponse(RabbitMQBase):
    """Policy definition values returned by policy endpoints."""

    model_config = RabbitMQBase.model_config | {"extra": "allow"}

    max_length: int | None = Field(None, alias="max-length")
    max_length_bytes: int | None = Field(None, alias="max-length-bytes")
    message_ttl: int | None = Field(None, alias="message-ttl")
    expires: int | None = None
    overflow: OverflowBehaviour | None = None
    dead_letter_exchange: str | None = Field(None, alias="dead-letter-exchange")
    dead_letter_routing_key: str | None = Field(None, alias="dead-letter-routing-key")
    dead_letter_strategy: DeadLetterStrategy | None = Field(None, alias="dead-letter-strategy")
    delivery_limit: int | None = Field(None, alias="delivery-limit")
    consumer_timeout: int | None = Field(None, alias="consumer-timeout")
    queue_leader_locator: QueueLeaderLocator | None = Field(None, alias="queue-leader-locator")
    max_age: str | None = Field(None, alias="max-age")
    stream_filter_size_bytes: int | None = Field(None, alias="stream-filter-size-bytes")
    max_in_memory_length: int | None = Field(None, alias="max-in-memory-length")
    max_in_memory_bytes: int | None = Field(None, alias="max-in-memory-bytes")
    target_group_size: int | None = Field(None, alias="target-group-size")
    alternate_exchange: str | None = Field(None, alias="alternate-exchange")


class PolicyResponse(RabbitMQBase):
    """One regular policy returned by RabbitMQ."""

    vhost: str
    name: str
    pattern: str
    definition: PolicyDefinitionResponse
    priority: int
    apply_to: PolicyApplyTo = Field(alias="apply-to")


class OperatorPolicyResponse(RabbitMQBase):
    """One operator policy returned by RabbitMQ."""

    vhost: str
    name: str
    pattern: str
    definition: PolicyDefinitionResponse
    priority: int
    apply_to: OperatorPolicyApplyTo = Field(alias="apply-to")
