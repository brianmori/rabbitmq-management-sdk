from typing import Any

from pydantic import Field

from rabbitmq_management_sdk.domains.base import RabbitMQBase


class BindingRequest(RabbitMQBase):
    """Payload for creating a binding.

    Used for both exchange-to-queue and exchange-to-exchange bindings.
    The source, destination, and destination type are part of the URL —
    only the routing key and arguments are sent in the body.

    Attributes:
        routing_key (str): Routing key for the binding. Empty string for fanout exchanges.
        arguments (dict[str, Any]): Additional binding arguments, e.g. for headers exchanges.

    """

    routing_key: str = Field(
        default="",
        description="Routing key for the binding. Empty string for fanout exchanges.",
    )
    arguments: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional binding arguments, e.g. for headers exchanges.",
    )
