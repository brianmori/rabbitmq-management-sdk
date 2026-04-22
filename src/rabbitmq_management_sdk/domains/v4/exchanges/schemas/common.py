from enum import StrEnum


class ExchangeType(StrEnum):
    """Built-in RabbitMQ exchange types.

    Plugin-provided exchange types (e.g., 'x-consistent-hash') are not
    enumerated here. Use raw strings for those types and install the plugin on the server to avoid errors.

    Attributes:
        DIRECT: Delivers messages to queues based on a routing key.
        FANOUT: Routes messages to all queues bound to it.
        TOPIC: Routes messages based on wildcard matching.
        HEADERS: Routes based on message header attributes.
    """

    DIRECT = "direct"
    FANOUT = "fanout"
    TOPIC = "topic"
    HEADERS = "headers"
