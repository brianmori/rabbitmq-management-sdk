from __future__ import annotations

from pydantic import Field, model_validator

from rabbitmq_management_sdk.resources.base import RabbitMQRequest
from rabbitmq_management_sdk.resources.v4.common import (
    DeadLetterStrategy,
    DelayedRetryType,
    OverflowBehaviour,
    QueueLeaderLocator,
    StreamMaxAge,
)
from rabbitmq_management_sdk.resources.v4.policies.schemas.common import (
    OperatorPolicyApplyTo,
    PolicyApplyTo,
)


class PolicyDefinition(RabbitMQRequest):
    """Definition values for regular and operator policies.

    RabbitMQ plugins can contribute definition keys, so unknown fields are
    retained and serialized alongside the typed core fields.
    """

    model_config = RabbitMQRequest.model_config | {"extra": "allow"}

    max_length: int | None = Field(None, alias="max-length")
    max_length_bytes: int | None = Field(None, alias="max-length-bytes")
    message_ttl: int | None = Field(None, alias="message-ttl")
    expires: int | None = None
    overflow: OverflowBehaviour | None = None
    dead_letter_exchange: str | None = Field(None, alias="dead-letter-exchange")
    dead_letter_routing_key: str | None = Field(None, alias="dead-letter-routing-key")
    dead_letter_strategy: DeadLetterStrategy | None = Field(None, alias="dead-letter-strategy")
    delivery_limit: int | None = Field(None, alias="delivery-limit")
    consumer_timeout: int | None = Field(None, alias="consumer-timeout", ge=0, strict=True)
    consumer_disconnected_timeout: int | None = Field(None, alias="consumer-disconnected-timeout", ge=0, strict=True)
    delayed_retry_type: DelayedRetryType | None = Field(None, alias="delayed-retry-type")
    delayed_retry_min: int | None = Field(None, alias="delayed-retry-min", gt=0, strict=True)
    delayed_retry_max: int | None = Field(None, alias="delayed-retry-max", gt=0, strict=True)
    queue_leader_locator: QueueLeaderLocator | None = Field(None, alias="queue-leader-locator")
    max_age: StreamMaxAge | None = Field(None, alias="max-age")
    stream_filter_size_bytes: int | None = Field(None, alias="stream-filter-size-bytes")
    max_in_memory_length: int | None = Field(None, alias="max-in-memory-length")
    max_in_memory_bytes: int | None = Field(None, alias="max-in-memory-bytes")
    target_group_size: int | None = Field(None, alias="target-group-size")
    alternate_exchange: str | None = Field(None, alias="alternate-exchange")

    @model_validator(mode="after")
    def validate_delayed_retry(self) -> PolicyDefinition:
        """Apply RabbitMQ 4.3's regular/operator policy retry constraints.

        Queue arguments deliberately have no corresponding cross-field check:
        their effective settings can come from both arguments and policy.
        """
        if self.delayed_retry_type not in (None, DelayedRetryType.DISABLED) and self.delayed_retry_min is None:
            raise ValueError("delayed_retry_min is required when delayed_retry_type is enabled")
        if (
            self.delayed_retry_min is not None
            and self.delayed_retry_max is not None
            and self.delayed_retry_max < self.delayed_retry_min
        ):
            raise ValueError("delayed_retry_max must not be smaller than delayed_retry_min")
        return self

    @property
    def effective_dead_letter_strategy(self) -> DeadLetterStrategy | None:
        """Return RabbitMQ's effective dead-lettering strategy."""
        if (
            self.dead_letter_strategy == DeadLetterStrategy.AT_LEAST_ONCE
            and self.overflow != OverflowBehaviour.REJECT_PUBLISH
        ):
            return DeadLetterStrategy.AT_MOST_ONCE
        return self.dead_letter_strategy


class PolicyRequest(RabbitMQRequest):
    """Payload for declaring or updating a regular policy.

    All four fields are required by RabbitMQ's HTTP API.
    """

    pattern: str
    definition: PolicyDefinition
    priority: int
    apply_to: PolicyApplyTo = Field(alias="apply-to")


class OperatorPolicyRequest(RabbitMQRequest):
    """Payload for declaring or updating an operator policy.

    Operator policies are restricted to queue and stream scopes.
    """

    pattern: str
    definition: PolicyDefinition
    priority: int
    apply_to: OperatorPolicyApplyTo = Field(alias="apply-to")
