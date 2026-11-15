"""Native Pydantic validation for extensible wire dictionaries."""

from collections.abc import Mapping
from typing import ClassVar, Self

from pydantic import Field, JsonValue, RootModel
from typing_extensions import TypedDict

from rabbitmq_management_sdk.resources.base import RabbitMQBase


# Mypy does not yet support PEP 728's extra_items class argument. Pydantic
# validates this schema at runtime, including every additional JSON value.
class JsonArgumentEntries(TypedDict, extra_items=JsonValue):  # type: ignore[call-arg]
    """Allow additional dictionary keys whose values are valid JSON."""


class ResponseArgumentMap[Values: Mapping[str, object]](RootModel[Values]):
    """Read wire dictionaries through Pydantic's ordinary validation pipeline.

    Concrete schemas declare optional, literal wire keys in a TypedDict that
    inherits JsonArgumentEntries. Constructors and all validation entry points
    share those rules. Python-name construction uses the explicit from_fields
    factory; it never alters the response parsing path.
    """

    model_config = RabbitMQBase.model_config | {"revalidate_instances": "always"}
    root: Values = Field(default_factory=dict, validate_default=True)
    _field_aliases: ClassVar[Mapping[str, str]] = {}

    @classmethod
    def from_wire(cls, data: object) -> Self:
        """Validate a captured dictionary through the standard entry point."""
        return cls.model_validate(data)

    @classmethod
    def from_fields(cls, **fields: object) -> Self:
        """Translate known Python names, then validate the complete dictionary.

        An explicit wire key takes precedence. When both forms are supplied,
        the Python spelling remains a separate custom dictionary entry.
        """
        for name, alias in cls._field_aliases.items():
            if name in fields and alias not in fields:
                fields[alias] = fields.pop(name)
        return cls.model_validate(fields)
