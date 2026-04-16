# domains/v4/vhost/schemas/vhost_request.py
from enum import StrEnum
from typing import Literal

from pydantic import Field

from rabbitmq_management_sdk.domains.base import RabbitMQBase


class VhostLimitName(StrEnum):
    """Valid vhost limit names accepted by the RabbitMQ Management API."""

    MAX_CONNECTIONS = "max-connections"
    MAX_QUEUES = "max-queues"


class VhostRequest(RabbitMQBase):
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    tracing: bool = False
    default_queue_type: Literal["classic", "quorum", "stream"] = "classic"


class VhostLimitRequest(RabbitMQBase):
    """Payload for ``PUT /api/vhost-limits/{vhost}/{limit}``.

    The vhost and limit name are part of the URL — only the value is sent
    in the request body.

    Attributes:
        value (int): The value to be set for the specific vhost limit.

    Example:
        >>> VhostLimitRequest(value=100)
    """

    value: int = Field(ge=-1, description="Limit must be -1 (unlimited), 0 (block) or a positive integer.")

    def to_api_payload(self) -> dict[str, int]:
        return {"value": self.value}
