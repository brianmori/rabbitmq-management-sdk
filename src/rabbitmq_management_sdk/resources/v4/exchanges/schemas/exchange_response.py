from __future__ import annotations

from typing import Any, ClassVar

from pydantic import AliasChoices, Field
from typing_extensions import TypedDict

from rabbitmq_management_sdk.resources._argument_map import JsonArgumentEntries, ResponseArgumentMap
from rabbitmq_management_sdk.resources.base import RabbitMQResponse
from rabbitmq_management_sdk.resources.v4.exchanges.schemas.common import ExchangeType

_ExchangeArgumentFields = TypedDict(
    "_ExchangeArgumentFields",
    {
        "alternate-exchange": str | None,
    },
    total=False,
)


class _ExchangeArgumentValues(_ExchangeArgumentFields, JsonArgumentEntries):
    """Known wire keys plus additional JSON entries."""


class ExchangeArgumentsResponse(ResponseArgumentMap[_ExchangeArgumentValues]):
    """Captured arguments with exact wire keys and typed accessors."""

    _field_aliases: ClassVar[dict[str, str]] = {
        "alternate_exchange": "alternate-exchange",
    }

    @property
    def alternate_exchange(self) -> str | None:
        return self.root.get("alternate-exchange")


class ExchangeResponse(RabbitMQResponse):
    """Represents an exchange as returned by the RabbitMQ Management API.

    Attributes:
        name: The unique name of the exchange within the vhost.
        vhost: The virtual host the exchange belongs to.
        type: The exchange type (e.g., 'direct', 'topic').
        durable: Whether the exchange survives, a broker restarts.
        auto_delete: Whether the exchange is deleted when all bindings are removed.
        internal: Whether the exchange is restricted to internal broker use.
        arguments: A dictionary of configuration arguments.
        message_stats: Optional message throughput statistics.
    """

    name: str
    vhost: str
    type: ExchangeType | str
    durable: bool
    auto_delete: bool = Field(validation_alias=AliasChoices("auto_delete", "auto-delete"))
    internal: bool
    policy: str | None = None

    arguments: ExchangeArgumentsResponse = Field(default_factory=ExchangeArgumentsResponse)

    message_stats: dict[str, Any] | None = None
