from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, ValidationError

from rabbitmq_management_sdk.exceptions import MalformedResponseError

if TYPE_CHECKING:
    from rabbitmq_management_sdk.http_adapter.base import HttpResponse


class RabbitMQBase(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True, use_enum_values=True)


@dataclass(frozen=True)
class Page[T]:
    """A page returned by a paginated RabbitMQ Management API endpoint.

    Attributes:
        items: Parsed resources in this page.
        page: One-based page number returned by RabbitMQ.
        page_count: Number of pages matching the request.
        page_size: Requested maximum number of resources per page.
        total_count: Number of resources before applying an optional name filter.
        filtered_count: Number of resources after applying an optional name filter.
        item_count: Number of resources in this page.
    """

    items: tuple[T, ...]
    page: int
    page_count: int
    page_size: int
    total_count: int
    filtered_count: int
    item_count: int


class _PageEnvelope(RabbitMQBase):
    """The common wire representation used by RabbitMQ paginated endpoints."""

    items: list[dict[str, object]]
    page: int
    page_count: int
    page_size: int
    total_count: int
    filtered_count: int
    item_count: int


def parse_one[T: RabbitMQBase](response: HttpResponse, model: type[T]) -> T:
    """Parse a single model from an HTTP response body.

    Args:
        response: The HTTP response to parse.
        model: The Pydantic model class to validate against.

    Returns:
        A validated model instance.

    Raises:
        MalformedResponseError: If the body cannot be decoded or does not match the schema.
    """
    try:
        return model.model_validate(response.json())
    except ValidationError as e:
        raise MalformedResponseError(f"Response body did not match {model.__name__}") from e


def parse_list[T: RabbitMQBase](response: HttpResponse, model: type[T]) -> list[T]:
    """Parse a list of models from an HTTP response body.

    Args:
        response: The HTTP response to parse.
        model: The Pydantic model class to validate each item against.

    Returns:
        A list of validated model instances.

    Raises:
        MalformedResponseError: If the body cannot be decoded or an item does not match the schema.
    """
    try:
        return [model.model_validate(item) for item in response.json()]
    except ValidationError as e:
        raise MalformedResponseError(f"Response body did not match list[{model.__name__}]") from e


def parse_page[T: RabbitMQBase](response: HttpResponse, model: type[T]) -> Page[T]:
    """Parse a paginated list of models from an HTTP response body.

    Args:
        response: The HTTP response to parse.
        model: The Pydantic model class to validate each item against.

    Returns:
        The parsed page and its RabbitMQ pagination metadata.

    Raises:
        MalformedResponseError: If the body does not match RabbitMQ's pagination envelope
            or an item does not match the schema.
    """
    try:
        envelope = _PageEnvelope.model_validate(response.json())
        items = tuple(model.model_validate(item) for item in envelope.items)
    except ValidationError as e:
        raise MalformedResponseError(f"Response body did not match page[{model.__name__}]") from e

    return Page(
        items=items,
        page=envelope.page,
        page_count=envelope.page_count,
        page_size=envelope.page_size,
        total_count=envelope.total_count,
        filtered_count=envelope.filtered_count,
        item_count=envelope.item_count,
    )
