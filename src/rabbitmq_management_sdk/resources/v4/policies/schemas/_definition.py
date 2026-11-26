"""Typed views over literal policy definition dictionaries."""

from typing import ClassVar

from typing_extensions import TypedDict

from rabbitmq_management_sdk.resources._argument_map import JsonArgumentEntries, ResponseArgumentMap
from rabbitmq_management_sdk.resources.v4.common import (
    CapturedPolicyLeaderLocator,
    DeadLetterStrategy,
    DelayedRetryType,
    OverflowBehaviour,
)

_PolicyFields = TypedDict(
    "_PolicyFields",
    {
        "max-length": int | None,
        "max-length-bytes": int | None,
        "message-ttl": int | None,
        "expires": int | None,
        "overflow": OverflowBehaviour | None,
        "dead-letter-exchange": str | None,
        "dead-letter-routing-key": str | None,
        "dead-letter-strategy": DeadLetterStrategy | None,
        "delivery-limit": int | None,
        "consumer-timeout": int | None,
        "consumer-disconnected-timeout": int | None,
        "delayed-retry-type": DelayedRetryType | None,
        "delayed-retry-min": int | None,
        "delayed-retry-max": int | None,
        "queue-leader-locator": CapturedPolicyLeaderLocator | None,
        "max-age": str | None,
        "stream-filter-size-bytes": int | None,
        "max-in-memory-length": int | None,
        "max-in-memory-bytes": int | None,
        "target-group-size": int | None,
        "alternate-exchange": str | None,
    },
    total=False,
)


class _PolicyValues(_PolicyFields, JsonArgumentEntries):
    """Known wire keys plus additional JSON entries."""


class _PolicyDefinitionMap(ResponseArgumentMap[_PolicyValues]):
    """Keep plugin data separate from recognized policy settings."""

    _field_aliases: ClassVar[dict[str, str]] = {
        "max_length": "max-length",
        "max_length_bytes": "max-length-bytes",
        "message_ttl": "message-ttl",
        "dead_letter_exchange": "dead-letter-exchange",
        "dead_letter_routing_key": "dead-letter-routing-key",
        "dead_letter_strategy": "dead-letter-strategy",
        "delivery_limit": "delivery-limit",
        "consumer_timeout": "consumer-timeout",
        "consumer_disconnected_timeout": "consumer-disconnected-timeout",
        "delayed_retry_type": "delayed-retry-type",
        "delayed_retry_min": "delayed-retry-min",
        "delayed_retry_max": "delayed-retry-max",
        "queue_leader_locator": "queue-leader-locator",
        "max_age": "max-age",
        "stream_filter_size_bytes": "stream-filter-size-bytes",
        "max_in_memory_length": "max-in-memory-length",
        "max_in_memory_bytes": "max-in-memory-bytes",
        "target_group_size": "target-group-size",
        "alternate_exchange": "alternate-exchange",
    }

    @property
    def max_length(self) -> int | None:
        return self.root.get("max-length")

    @property
    def max_length_bytes(self) -> int | None:
        return self.root.get("max-length-bytes")

    @property
    def message_ttl(self) -> int | None:
        return self.root.get("message-ttl")

    @property
    def expires(self) -> int | None:
        return self.root.get("expires")

    @property
    def overflow(self) -> OverflowBehaviour | None:
        return self.root.get("overflow")

    @property
    def dead_letter_exchange(self) -> str | None:
        return self.root.get("dead-letter-exchange")

    @property
    def dead_letter_routing_key(self) -> str | None:
        return self.root.get("dead-letter-routing-key")

    @property
    def dead_letter_strategy(self) -> DeadLetterStrategy | None:
        return self.root.get("dead-letter-strategy")

    @property
    def delivery_limit(self) -> int | None:
        return self.root.get("delivery-limit")

    @property
    def consumer_timeout(self) -> int | None:
        return self.root.get("consumer-timeout")

    @property
    def consumer_disconnected_timeout(self) -> int | None:
        return self.root.get("consumer-disconnected-timeout")

    @property
    def delayed_retry_type(self) -> DelayedRetryType | None:
        return self.root.get("delayed-retry-type")

    @property
    def delayed_retry_min(self) -> int | None:
        return self.root.get("delayed-retry-min")

    @property
    def delayed_retry_max(self) -> int | None:
        return self.root.get("delayed-retry-max")

    @property
    def queue_leader_locator(self) -> CapturedPolicyLeaderLocator | None:
        return self.root.get("queue-leader-locator")

    @property
    def max_age(self) -> str | None:
        return self.root.get("max-age")

    @property
    def stream_filter_size_bytes(self) -> int | None:
        return self.root.get("stream-filter-size-bytes")

    @property
    def max_in_memory_length(self) -> int | None:
        return self.root.get("max-in-memory-length")

    @property
    def max_in_memory_bytes(self) -> int | None:
        return self.root.get("max-in-memory-bytes")

    @property
    def target_group_size(self) -> int | None:
        return self.root.get("target-group-size")

    @property
    def alternate_exchange(self) -> str | None:
        return self.root.get("alternate-exchange")
