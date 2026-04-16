# domains/v4/vhost/schemas/vhost_response.py
from typing import TYPE_CHECKING, Literal

from pydantic import Field

from rabbitmq_management_sdk.domains.base import RabbitMQBase

if TYPE_CHECKING:
    from rabbitmq_management_sdk.domains.v4.admin.schemas.enums import VhostLimitName


class VhostResponse(RabbitMQBase):
    name: str
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    tracing: bool
    default_queue_type: Literal["classic", "quorum", "stream"] | None = None
    cluster_state: dict[str, str] | None = None


class VhostLimitResponse(RabbitMQBase):
    """A single vhost limit as returned by the Management API.

    Returned by both `GET /api/vhost-limits/{vhost}` and `GET /api/vhost-limits`.
    The `vhost` field is always present, which is useful when listing limits
    across the entire cluster.

    Example:
        >>> {"vhost": "production", "limit": "max-connections", "value": 100}
    """

    vhost: str
    name: VhostLimitName
    value: int
