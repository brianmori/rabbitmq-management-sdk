from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from rabbitmq_management_sdk.resources.base import RabbitMQBase
from rabbitmq_management_sdk.resources.v4.shovels.schemas.common import AckMode, DeleteAfter

# ---------------------------------------------------------------------------
# Source endpoint — per-protocol variants
# ---------------------------------------------------------------------------


class Amqp091ShovelSource(RabbitMQBase):
    src_protocol: Literal["amqp091"] = Field("amqp091", alias="src-protocol", frozen=True)
    src_queue: str | None = Field(None, alias="src-queue")
    src_queue_args: dict[str, object] | None = Field(None, alias="src-queue-args")
    src_exchange: str | None = Field(None, alias="src-exchange")
    src_exchange_key: str | None = Field(None, alias="src-exchange-key")
    src_predeclared: bool = Field(False, alias="src-predeclared")
    src_consumer_name: str | None = Field(None, alias="src-consumer-name")
    src_consumer_args: dict[str, object] | None = Field(None, alias="src-consumer-args")
    src_prefetch_count: int = Field(1000, alias="src-prefetch-count")

    @model_validator(mode="after")
    def _check_queue_xor_exchange(self) -> Amqp091ShovelSource:
        has_queue = self.src_queue is not None
        has_exchange = self.src_exchange is not None
        if has_queue and has_exchange:
            raise ValueError("src-queue and src-exchange are mutually exclusive")
        if not has_queue and not has_exchange:
            raise ValueError("One of src-queue or src-exchange is required")
        if has_exchange and self.src_exchange_key is None:
            raise ValueError("src-exchange-key is required when src-exchange is set")
        return self


class LocalShovelSource(RabbitMQBase):
    """Source config for local protocol (RabbitMQ 4.2+).

    Uses an internal cluster API instead of a TCP connection —
    higher throughput, no TLS, same-cluster only.
    """

    src_protocol: Literal["local"] = Field("local", alias="src-protocol", frozen=True)
    src_queue: str | None = Field(None, alias="src-queue")
    src_queue_args: dict[str, object] | None = Field(None, alias="src-queue-args")
    src_exchange: str | None = Field(None, alias="src-exchange")
    src_exchange_key: str | None = Field(None, alias="src-exchange-key")
    src_predeclared: bool = Field(False, alias="src-predeclared")
    src_consumer_name: str | None = Field(None, alias="src-consumer-name")
    src_consumer_args: dict[str, object] | None = Field(None, alias="src-consumer-args")
    src_prefetch_count: int = Field(1000, alias="src-prefetch-count")

    @model_validator(mode="after")
    def _check_queue_xor_exchange(self) -> LocalShovelSource:
        has_queue = self.src_queue is not None
        has_exchange = self.src_exchange is not None
        if has_queue and has_exchange:
            raise ValueError("src-queue and src-exchange are mutually exclusive")
        if not has_queue and not has_exchange:
            raise ValueError("One of src-queue or src-exchange is required")
        if has_exchange and self.src_exchange_key is None:
            raise ValueError("src-exchange-key is required when src-exchange is set")
        return self


class Amqp10ShovelSource(RabbitMQBase):
    src_protocol: Literal["amqp10"] = Field("amqp10", alias="src-protocol", frozen=True)
    src_address: str = Field(alias="src-address")
    src_prefetch_count: int = Field(1000, alias="src-prefetch-count")


type ShovelSourceArguments = Annotated[
    Amqp091ShovelSource | LocalShovelSource | Amqp10ShovelSource,
    Field(discriminator="src_protocol"),
]


# ---------------------------------------------------------------------------
# Destination endpoint — per-protocol variants
# ---------------------------------------------------------------------------


class Amqp091ShovelDestination(RabbitMQBase):
    dest_protocol: Literal["amqp091"] = Field("amqp091", alias="dest-protocol", frozen=True)
    dest_queue: str | None = Field(None, alias="dest-queue")
    dest_queue_args: dict[str, object] | None = Field(None, alias="dest-queue-args")
    dest_exchange: str | None = Field(None, alias="dest-exchange")
    dest_exchange_key: str | None = Field(None, alias="dest-exchange-key")
    dest_predeclared: bool = Field(False, alias="dest-predeclared")
    dest_add_forward_headers: bool = Field(False, alias="dest-add-forward-headers")
    dest_add_timestamp_header: bool = Field(False, alias="dest-add-timestamp-header")
    dest_publish_properties: dict[str, object] | None = Field(None, alias="dest-publish-properties")

    @model_validator(mode="after")
    def _check_queue_xor_exchange(self) -> Amqp091ShovelDestination:
        if self.dest_queue is not None and self.dest_exchange is not None:
            raise ValueError("dest-queue and dest-exchange are mutually exclusive")
        return self


class LocalShovelDestination(RabbitMQBase):
    """Destination config for local protocol (RabbitMQ 4.2+).

    Same field set as amqp091 destination — local shovels share
    most configuration with AMQP 0-9-1.
    """

    dest_protocol: Literal["local"] = Field("local", alias="dest-protocol", frozen=True)
    dest_queue: str | None = Field(None, alias="dest-queue")
    dest_queue_args: dict[str, object] | None = Field(None, alias="dest-queue-args")
    dest_exchange: str | None = Field(None, alias="dest-exchange")
    dest_exchange_key: str | None = Field(None, alias="dest-exchange-key")
    dest_predeclared: bool = Field(False, alias="dest-predeclared")
    dest_add_forward_headers: bool = Field(False, alias="dest-add-forward-headers")
    dest_add_timestamp_header: bool = Field(False, alias="dest-add-timestamp-header")
    dest_publish_properties: dict[str, object] | None = Field(None, alias="dest-publish-properties")

    @model_validator(mode="after")
    def _check_queue_xor_exchange(self) -> LocalShovelDestination:
        if self.dest_queue is not None and self.dest_exchange is not None:
            raise ValueError("dest-queue and dest-exchange are mutually exclusive")
        return self


class Amqp10ShovelDestination(RabbitMQBase):
    dest_protocol: Literal["amqp10"] = Field("amqp10", alias="dest-protocol", frozen=True)
    dest_address: str = Field(alias="dest-address")
    dest_application_properties: dict[str, str | int | float | bool] | None = Field(
        None, alias="dest-application-properties"
    )
    dest_properties: dict[str, str | int | float | bool] | None = Field(None, alias="dest-properties")
    dest_message_annotations: dict[str, str | int | float | bool] | None = Field(None, alias="dest-message-annotations")
    dest_add_forward_headers: bool = Field(False, alias="dest-add-forward-headers")
    dest_add_timestamp_header: bool = Field(False, alias="dest-add-timestamp-header")


type ShovelDestinationArguments = Annotated[
    Amqp091ShovelDestination | LocalShovelDestination | Amqp10ShovelDestination,
    Field(discriminator="dest_protocol"),
]


# ---------------------------------------------------------------------------
# ShovelRequest — mirrors QueueRequest: common fields + nested variant args
# ---------------------------------------------------------------------------


class ShovelRequest(RabbitMQBase):
    """Full payload for PUT /api/parameters/shovel/{vhost}/{name}.

    Common transfer fields sit at the top level alongside src-uri and
    dest-uri. Protocol-specific topology fields are nested under
    src_arguments and dest_arguments, matching the queue pattern where
    queue-type-specific x-args live in a nested `arguments` model.

    Wire serialization is handled by to_api_value() which flattens the
    nested models into the single flat JSON object the API expects.

    Example — local to amqp091::

        ShovelRequest(
            src_uri="amqp://localhost",
            dest_uri="amqp://remote-host",
            src_arguments=LocalShovelSource(src_queue="source.q"),
            dest_arguments=Amqp091ShovelDestination(dest_queue="dest.q"),
        )

    Attributes:
        src_uri: Source URI
        dest_uri: Destination URI
        src_arguments: ShovelSourceArguments
        dest_arguments: ShovelDestinationArguments
    """

    # -- Connection (common to all protocols) --------------------------------
    src_uri: str | list[str] = Field(alias="src-uri")
    dest_uri: str | list[str] = Field(alias="dest-uri")

    # -- Protocol-specific topology ------------------------------------------
    src_arguments: ShovelSourceArguments
    dest_arguments: ShovelDestinationArguments

    # -- Transfer behaviour (common to all protocols) ------------------------
    ack_mode: AckMode = Field(AckMode.ON_CONFIRM, alias="ack-mode")
    reconnect_delay: int = Field(1, alias="reconnect-delay")
    src_delete_after: str | int = Field(DeleteAfter.NEVER, alias="src-delete-after")

    @field_validator("src_delete_after", mode="before")
    @classmethod
    def _validate_delete_after(cls, v: object) -> str | int:
        if isinstance(v, int):
            if v < 0:
                raise ValueError("src-delete-after integer must be >= 0")
            return v
        if isinstance(v, str):
            if v not in {DeleteAfter.NEVER, DeleteAfter.QUEUE_LENGTH}:
                raise ValueError(
                    f"src-delete-after must be '{DeleteAfter.NEVER}', "
                    f"'{DeleteAfter.QUEUE_LENGTH}', or a non-negative int; got {v!r}"
                )
            return v
        raise ValueError("src-delete-after must be a string or int")

    @model_validator(mode="after")
    def _delete_after_no_ack_guard(self) -> ShovelRequest:
        if isinstance(self.src_delete_after, int) and self.ack_mode == AckMode.NO_ACK:
            raise ValueError("src-delete-after integer cannot be combined with ack-mode='no-ack'")
        return self

    def to_api_value(self) -> dict[str, object]:
        """Flatten src_arguments and dest_arguments into the single JSON
        object the management API expects as the 'value' key.

        src-uri and dest-uri live on ShovelRequest itself (not in the
        nested models) so they serialize naturally here.
        """
        base = self.model_dump(
            by_alias=True,
            exclude_none=True,
            exclude={"src_arguments", "dest_arguments"},
        )
        src = self.src_arguments.model_dump(by_alias=True, exclude_none=True)
        dest = self.dest_arguments.model_dump(by_alias=True, exclude_none=True)
        return base | src | dest


# ---------------------------------------------------------------------------
# Management API envelope
# ---------------------------------------------------------------------------


class UpsertShovelRequest(RabbitMQBase):
    """Request body for PUT /api/parameters/shovel/{vhost}/{name}."""

    value: ShovelRequest

    def to_api_body(self) -> dict[str, object]:
        return {"value": self.value.to_api_value()}
