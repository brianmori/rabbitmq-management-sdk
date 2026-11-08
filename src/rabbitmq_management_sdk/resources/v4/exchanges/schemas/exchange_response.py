from __future__ import annotations

from typing import Annotated, Any

from pydantic import AliasChoices, BeforeValidator, Field

from rabbitmq_management_sdk.resources._arguments import _WireArguments
from rabbitmq_management_sdk.resources.base import RabbitMQBase
from rabbitmq_management_sdk.resources.v4.exchanges.schemas.common import ExchangeType


class ExchangeArgumentsResponse(_WireArguments):
    """Arguments echoed back by the server for an exchange.

    Attributes:
        alternate_exchange: The name of the configured alternate exchange.
    """

    alternate_exchange: str | None = Field(default=None, alias="alternate-exchange")


class ExchangeResponse(RabbitMQBase):
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

    arguments: Annotated[ExchangeArgumentsResponse, BeforeValidator(ExchangeArgumentsResponse.from_wire)] = Field(
        default_factory=ExchangeArgumentsResponse
    )

    message_stats: dict[str, Any] | None = None
