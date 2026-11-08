"""Literal wire-dictionary ingestion for extensible response arguments."""

from collections.abc import Mapping
from typing import Self

from rabbitmq_management_sdk.resources.base import RabbitMQBase


class _WireArguments(RabbitMQBase):
    """Keep named-field construction separate from captured dictionary keys."""

    model_config = RabbitMQBase.model_config | {"extra": "allow"}

    @classmethod
    def from_wire(cls, data: object) -> Self:
        """Validate literal wire keys, or retain an explicitly built model.

        Response fields use this before nested validation. Direct argument
        model constructors still accept Python field names as a convenience.
        """
        if not isinstance(data, Mapping):
            return cls.model_validate(data)

        arguments = cls.model_validate(data, by_alias=True, by_name=False)
        # Pydantic shares field-set tracking between fields and extras. An extra
        # named like a field must not mark that field's absent wire alias as set:
        # exclude_unset would otherwise synthesize that alias with its default.
        # Extras themselves are serialized independently of this field set.
        for name, field in cls.model_fields.items():
            if field.alias is not None and field.alias != name and field.alias not in data:
                arguments.__pydantic_fields_set__.discard(name)
        return arguments
