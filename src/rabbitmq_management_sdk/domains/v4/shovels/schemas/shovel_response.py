# rabbitmq_management_sdk/domains/shovel.py  (response additions)
from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from rabbitmq_management_sdk.domains.base import RabbitMQBase
from rabbitmq_management_sdk.domains.v4.shovels.schemas.common import AckMode, DeleteAfter

# ---------------------------------------------------------------------------
# Parameters API response
# GET /api/parameters/shovel/{vhost}
# GET /api/parameters/shovel/{vhost}/{name}
#
# The server echoes the stored definition back verbatim.
# value is the same flat object we PUT, so we reuse the per-protocol
# source/destination response variants — same pattern as QueueResponse
# containing QueueResponseArguments.
# ---------------------------------------------------------------------------


class Amqp091ShovelSourceResponse(RabbitMQBase):
    src_protocol: Literal["amqp091"] = Field(alias="src-protocol")
    src_queue: str | None = Field(None, alias="src-queue")
    src_queue_args: dict[str, object] | None = Field(None, alias="src-queue-args")
    src_exchange: str | None = Field(None, alias="src-exchange")
    src_exchange_key: str | None = Field(None, alias="src-exchange-key")
    src_predeclared: bool = Field(False, alias="src-predeclared")
    src_consumer_name: str | None = Field(None, alias="src-consumer-name")
    src_consumer_args: dict[str, object] | None = Field(None, alias="src-consumer-args")
    src_prefetch_count: int = Field(1000, alias="src-prefetch-count")


class LocalShovelSourceResponse(RabbitMQBase):
    src_protocol: Literal["local"] = Field(alias="src-protocol")
    src_queue: str | None = Field(None, alias="src-queue")
    src_queue_args: dict[str, object] | None = Field(None, alias="src-queue-args")
    src_exchange: str | None = Field(None, alias="src-exchange")
    src_exchange_key: str | None = Field(None, alias="src-exchange-key")
    src_predeclared: bool = Field(False, alias="src-predeclared")
    src_consumer_name: str | None = Field(None, alias="src-consumer-name")
    src_consumer_args: dict[str, object] | None = Field(None, alias="src-consumer-args")
    src_prefetch_count: int = Field(1000, alias="src-prefetch-count")


class Amqp10ShovelSourceResponse(RabbitMQBase):
    src_protocol: Literal["amqp10"] = Field(alias="src-protocol")
    src_address: str = Field(alias="src-address")
    src_consumer_name: str | None = Field(None, alias="src-consumer-name")
    src_prefetch_count: int = Field(1000, alias="src-prefetch-count")


type ShovelSourceArgumentsResponse = Annotated[
    Amqp091ShovelSourceResponse | LocalShovelSourceResponse | Amqp10ShovelSourceResponse,
    Field(discriminator="src_protocol"),
]


class Amqp091ShovelDestinationResponse(RabbitMQBase):
    dest_protocol: Literal["amqp091"] = Field(alias="dest-protocol")
    dest_queue: str | None = Field(None, alias="dest-queue")
    dest_queue_args: dict[str, object] | None = Field(None, alias="dest-queue-args")
    dest_exchange: str | None = Field(None, alias="dest-exchange")
    dest_exchange_key: str | None = Field(None, alias="dest-exchange-key")
    dest_predeclared: bool = Field(False, alias="dest-predeclared")
    dest_add_forward_headers: bool = Field(False, alias="dest-add-forward-headers")
    dest_add_timestamp_header: bool = Field(False, alias="dest-add-timestamp-header")
    dest_publish_properties: dict[str, object] | None = Field(None, alias="dest-publish-properties")


class LocalShovelDestinationResponse(RabbitMQBase):
    dest_protocol: Literal["local"] = Field(alias="dest-protocol")
    dest_queue: str | None = Field(None, alias="dest-queue")
    dest_queue_args: dict[str, object] | None = Field(None, alias="dest-queue-args")
    dest_exchange: str | None = Field(None, alias="dest-exchange")
    dest_exchange_key: str | None = Field(None, alias="dest-exchange-key")
    dest_predeclared: bool = Field(False, alias="dest-predeclared")
    dest_add_forward_headers: bool = Field(False, alias="dest-add-forward-headers")
    dest_add_timestamp_header: bool = Field(False, alias="dest-add-timestamp-header")
    dest_publish_properties: dict[str, object] | None = Field(None, alias="dest-publish-properties")


class Amqp10ShovelDestinationResponse(RabbitMQBase):
    dest_protocol: Literal["amqp10"] = Field(alias="dest-protocol")
    dest_address: str = Field(alias="dest-address")
    dest_application_properties: dict[str, str | int | float | bool] | None = Field(
        None, alias="dest-application-properties"
    )
    dest_properties: dict[str, str | int | float | bool] | None = Field(None, alias="dest-properties")
    dest_message_annotations: dict[str, str | int | float | bool] | None = Field(None, alias="dest-message-annotations")
    dest_add_forward_headers: bool = Field(False, alias="dest-add-forward-headers")
    dest_add_timestamp_header: bool = Field(False, alias="dest-add-timestamp-header")


type ShovelDestinationArgumentsResponse = Annotated[
    Amqp091ShovelDestinationResponse | LocalShovelDestinationResponse | Amqp10ShovelDestinationResponse,
    Field(discriminator="dest_protocol"),
]


class ShovelResponse(RabbitMQBase):
    """The value object inside GET /api/parameters/shovel/{vhost}/{name}.

    The server echoes the stored definition flat — same shape as the PUT
    body — so we parse it the same way ShovelRequest is constructed:
    common fields at the top level, protocol-specific topology nested
    into src_arguments / dest_arguments via a mode="before" validator
    that splits the flat dict before Pydantic touches it.

    Mirrors QueueResponse containing QueueResponseArguments.
    """

    # -- Connection ----------------------------------------------------------
    src_uri: str | list[str] = Field(alias="src-uri")
    dest_uri: str | list[str] = Field(alias="dest-uri")

    # -- Protocol-specific topology ------------------------------------------
    src_arguments: ShovelSourceArgumentsResponse
    dest_arguments: ShovelDestinationArgumentsResponse

    # -- Transfer behaviour --------------------------------------------------
    ack_mode: AckMode = Field(AckMode.ON_CONFIRM, alias="ack-mode")
    reconnect_delay: int = Field(1, alias="reconnect-delay")
    src_delete_after: str | int = Field(DeleteAfter.NEVER, alias="src-delete-after")

    @model_validator(mode="before")
    @classmethod
    def _split_flat_value(cls, data: object) -> object:
        """The wire format is a single flat dict. Split it into the nested
        src_arguments / dest_arguments sub-dicts before field validation
        runs, keyed by the discriminator fields src-protocol / dest-protocol.

        This is the parsing-boundary reshape — mode="before" is correct
        here because we are reshaping raw input, not validating domain rules.
        """
        if not isinstance(data, dict):
            return data

        src_keys = {
            "src-protocol",
            "src-queue",
            "src-queue-args",
            "src-exchange",
            "src-exchange-key",
            "src-predeclared",
            "src-consumer-name",
            "src-consumer-args",
            "src-prefetch-count",
            "src-address",
        }
        dest_keys = {
            "dest-protocol",
            "dest-queue",
            "dest-queue-args",
            "dest-exchange",
            "dest-exchange-key",
            "dest-predeclared",
            "dest-add-forward-headers",
            "dest-add-timestamp-header",
            "dest-publish-properties",
            "dest-address",
            "dest-application-properties",
            "dest-properties",
            "dest-message-annotations",
        }

        src_arguments = {k: v for k, v in data.items() if k in src_keys}
        dest_arguments = {k: v for k, v in data.items() if k in dest_keys}

        # default src-protocol when omitted (API default is amqp091)
        src_arguments.setdefault("src-protocol", "amqp091")
        dest_arguments.setdefault("dest-protocol", "amqp091")

        remainder = {k: v for k, v in data.items() if k not in src_keys and k not in dest_keys}

        return remainder | {"src_arguments": src_arguments, "dest_arguments": dest_arguments}


class ShovelParameterResponse(RabbitMQBase):
    """Envelope for GET /api/parameters/shovel/{vhost}/{name}.

    Wire shape:
        {
            "component": "shovel",
            "vhost":     "/",
            "name":      "my-shovel",
            "value":     { <flat ShovelResponse fields> }
        }
    """

    component: Literal["shovel"]
    vhost: str
    name: str
    value: ShovelResponse


# ---------------------------------------------------------------------------
# Shovel status API response
# GET /api/shovels
# GET /api/shovels/{vhost}
# GET /api/shovels/vhost/{vhost}/{name}
#
# Entirely different structure from the parameters API: flat, underscored
# keys, runtime counters. Not a definition echo — a live status snapshot.
# No nested arguments; no discriminated union needed.
# ---------------------------------------------------------------------------


class ShovelState(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    STOPPED = "stopped"
    TERMINATED = "terminated"
    FLOW = "flow"


class ShovelStatusResponse(RabbitMQBase):
    """Runtime status for GET /api/shovels/vhost/{vhost}/{name}.

    Note: keys here use underscores, not hyphens — the status API
    serialises differently from the parameters API.
    """

    # -- Identity ------------------------------------------------------------
    name: str
    vhost: str
    type: Literal["dynamic", "static"]
    node: str

    # -- Runtime state -------------------------------------------------------
    state: ShovelState
    timestamp: str | None = None
    blocked_status: str | None = None

    # -- Counters ------------------------------------------------------------
    pending: int | None = None
    forwarded: int | None = None
    remaining: int | str | None = None
    remaining_unacked: int | str | None = None

    # -- Connection summary (protocol-agnostic echo) -------------------------
    src_uri: str | list[str] | None = None
    src_protocol: str | None = None
    src_queue: str | None = None
    src_exchange: str | None = None
    src_address: str | None = None
    dest_uri: str | list[str] | None = None
    dest_protocol: str | None = None
    dest_queue: str | None = None
    dest_exchange: str | None = None
    dest_address: str | None = None
