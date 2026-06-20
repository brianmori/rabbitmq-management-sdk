from enum import StrEnum


class AckMode(StrEnum):
    ON_CONFIRM = "on-confirm"
    ON_PUBLISH = "on-publish"
    NO_ACK = "no-ack"


class DeleteAfter(StrEnum):
    NEVER = "never"
    QUEUE_LENGTH = "queue-length"
