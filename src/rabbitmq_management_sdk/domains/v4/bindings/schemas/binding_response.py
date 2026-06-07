from typing import Any

from pydantic import Field

from rabbitmq_management_sdk.domains.base import RabbitMQBase
from rabbitmq_management_sdk.domains.v4.bindings.schemas.common import BindingDestinationType


class BindingResponse(RabbitMQBase):
    """Represents a binding as returned by the RabbitMQ Management API.

    The ``properties_key`` is assigned by the server and required for
    deletion — store it if you need to delete a specific binding later.

    Example:

        binding = BindingResponse.model_validate(api_response)
        binding.source # exchange name
        binding.destination # queue or exchange name
        binding.destination_type # BindingDestinationType.QUEUE or .EXCHANGE
        binding.properties_key # use this to delete the binding
    """

    source: str
    destination: str
    destination_type: BindingDestinationType
    routing_key: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    vhost: str
    properties_key: str
