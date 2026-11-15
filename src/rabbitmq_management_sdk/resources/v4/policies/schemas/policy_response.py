from __future__ import annotations

from pydantic import Field

from rabbitmq_management_sdk.resources.base import RabbitMQResponse
from rabbitmq_management_sdk.resources.v4.policies.schemas._definition import _PolicyDefinitionMap
from rabbitmq_management_sdk.resources.v4.policies.schemas.common import (
    OperatorPolicyApplyTo,
    PolicyApplyTo,
)


class PolicyDefinitionResponse(_PolicyDefinitionMap):
    """Policy definition values returned by policy endpoints."""


class PolicyResponse(RabbitMQResponse):
    """One regular policy returned by RabbitMQ."""

    vhost: str
    name: str
    pattern: str
    definition: PolicyDefinitionResponse
    priority: int
    apply_to: PolicyApplyTo = Field(alias="apply-to")


class OperatorPolicyResponse(RabbitMQResponse):
    """One operator policy returned by RabbitMQ."""

    vhost: str
    name: str
    pattern: str
    definition: PolicyDefinitionResponse
    priority: int
    apply_to: OperatorPolicyApplyTo = Field(alias="apply-to")
