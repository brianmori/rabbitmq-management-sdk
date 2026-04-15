from enum import StrEnum


class VhostLimitName(StrEnum):
    """Valid vhost limit names accepted by the RabbitMQ Management API."""

    MAX_CONNECTIONS = "max-connections"
    MAX_QUEUES = "max-queues"
