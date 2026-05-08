from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, ValidationError

from rabbitmq_management_sdk.exceptions import MalformedResponseError

if TYPE_CHECKING:
    from rabbitmq_management_sdk.http_adapter.base import HttpResponse


class RabbitMQBase(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True, use_enum_values=True)


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
