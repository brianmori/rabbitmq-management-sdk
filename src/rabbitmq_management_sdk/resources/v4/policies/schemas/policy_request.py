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


class PolicyDefinition(RabbitMQBase):
    """Definition values for regular and operator policies.

    RabbitMQ plugins can contribute definition keys, so unknown fields are
    retained and serialized alongside the typed core fields.
    """

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

    @property
    def effective_dead_letter_strategy(self) -> DeadLetterStrategy | None:
        """Return RabbitMQ's effective dead-lettering strategy."""
        if (
            self.dead_letter_strategy == DeadLetterStrategy.AT_LEAST_ONCE
            and self.overflow != OverflowBehaviour.REJECT_PUBLISH
        ):
            return DeadLetterStrategy.AT_MOST_ONCE
        return self.dead_letter_strategy


class PolicyRequest(RabbitMQBase):
    """Payload for declaring or updating a regular policy.

    All four fields are required by RabbitMQ's HTTP API.
    """

    pattern: str
    definition: PolicyDefinition
    priority: int
    apply_to: PolicyApplyTo = Field(alias="apply-to")


class OperatorPolicyRequest(RabbitMQBase):
    """Payload for declaring or updating an operator policy.

    Operator policies are restricted to queue and stream scopes.
    """

    pattern: str
    definition: PolicyDefinition
    priority: int
    apply_to: OperatorPolicyApplyTo = Field(alias="apply-to")
