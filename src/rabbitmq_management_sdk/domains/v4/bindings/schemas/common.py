from enum import StrEnum


class BindingDestinationType(StrEnum):
    """Binding destination: a queue or an exchange."""

    QUEUE = "queue"
    EXCHANGE = "exchange"
