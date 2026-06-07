from pydantic import ConfigDict, Field

from rabbitmq_management_sdk.domains.base import RabbitMQBase
from rabbitmq_management_sdk.domains.v4.exchanges.schemas.common import ExchangeType


class ExchangeArguments(RabbitMQBase):
    """Optional arguments for exchange declaration.

    Attributes:
        alternate_exchange: The name of an exchange to route to if a message
            cannot be routed. Mapped to 'alternate-exchange' in the API.
    """

    model_config = ConfigDict(extra="forbid")  # Strict validation for arguments

    alternate_exchange: str | None = Field(
        default=None, alias="alternate-exchange", description="Optional alternate exchange name."
    )


class ExchangeRequest(RabbitMQBase):
    """Payload for creating or updating a RabbitMQ exchange.

    This model represents the body for 'PUT /api/exchanges/{vhost}/{name}'.
    The exchange type is immutable once the exchange is created.

    Attributes:
        type: The exchange type. Built-in types use ExchangeType;
            custom types use raw strings.
        durable: If True, the exchange survives server restarts.
        auto_delete: If True, the exchange is deleted when its last queue
            is unbound.
        internal: If True, messages cannot be published directly by clients.
        arguments: Additional exchange-specific arguments.

    Example:
        >>> request = ExchangeRequest(
        ...     type=ExchangeType.DIRECT, arguments=ExchangeArguments(alternate_exchange="my-exchange")
        ... )
    """

    type: ExchangeType | str = Field(
        default=ExchangeType.DIRECT, description="Exchange type (e.g., 'direct', 'topic')."
    )
    durable: bool = Field(default=True, description="Exchange persists after restart.")
    auto_delete: bool = Field(default=False, alias="auto-delete")
    internal: bool = Field(default=False)
    arguments: ExchangeArguments = Field(default_factory=ExchangeArguments)
