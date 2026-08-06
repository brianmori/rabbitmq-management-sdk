"""Typed Python SDK for the RabbitMQ HTTP Management API.

The public surface is re-exported here so callers can import everything they need
from the package root, e.g.::

    from rabbitmq_management_sdk import RabbitMQClient, Config, QueueRequest
"""

from __future__ import annotations

from rabbitmq_management_sdk.client.cluster_auditor import ClusterAuditor, TopologyAuditReport
from rabbitmq_management_sdk.client.config import Config, RabbitMQVersion, SSLConfig
from rabbitmq_management_sdk.client.rabbitmq_client import RabbitMQClient
from rabbitmq_management_sdk.exceptions import (
    APIError,
    BadRequestError,
    ConflictError,
    ConnectionError,
    ForbiddenError,
    MalformedResponseError,
    MethodNotAllowedError,
    NotFoundError,
    PreconditionFailedError,
    RabbitMQError,
    ServerError,
    ServiceUnavailableError,
    TimeoutError,
    TooManyRequestsError,
    TopologyAnalysisError,
    TopologyDefinitionsError,
    TopologyError,
    TopologyLoadError,
    TopologyParseError,
    TopologyResourceSnapshotError,
    TopologyValidationError,
    TransportError,
    UnauthorizedError,
    UnprocessableEntityError,
)
from rabbitmq_management_sdk.resources.base import Page
from rabbitmq_management_sdk.resources.v4.admin.schemas.vhost_request import (
    VhostLimitName,
    VhostLimitRequest,
    VhostRequest,
)
from rabbitmq_management_sdk.resources.v4.admin.schemas.vhost_response import (
    VhostLimitResponse,
    VhostLimitValues,
    VhostResponse,
)
from rabbitmq_management_sdk.resources.v4.bindings.schemas.binding_request import BindingRequest
from rabbitmq_management_sdk.resources.v4.bindings.schemas.binding_response import BindingResponse
from rabbitmq_management_sdk.resources.v4.bindings.schemas.common import BindingDestinationType
from rabbitmq_management_sdk.resources.v4.common import (
    DeadLetterStrategy,
    OverflowBehaviour,
    QueueLeaderLocator,
)
from rabbitmq_management_sdk.resources.v4.exchanges.schemas.common import ExchangeType
from rabbitmq_management_sdk.resources.v4.exchanges.schemas.exchange_request import ExchangeArguments, ExchangeRequest
from rabbitmq_management_sdk.resources.v4.exchanges.schemas.exchange_response import ExchangeResponse
from rabbitmq_management_sdk.resources.v4.policies.schemas.common import (
    OperatorPolicyApplyTo,
    PolicyApplyTo,
)
from rabbitmq_management_sdk.resources.v4.policies.schemas.policy_request import (
    OperatorPolicyRequest,
    PolicyDefinition,
    PolicyRequest,
)
from rabbitmq_management_sdk.resources.v4.policies.schemas.policy_response import (
    OperatorPolicyResponse,
    PolicyDefinitionResponse,
    PolicyResponse,
)
from rabbitmq_management_sdk.resources.v4.queues.schemas.queue_request import (
    ClassicQueueRequest,
    QueueDeleteOptions,
    QueueRequest,
    QuorumQueueOverflow,
    QuorumQueueRequest,
    StreamQueueRequest,
)
from rabbitmq_management_sdk.resources.v4.queues.schemas.queue_response import QueueResponse
from rabbitmq_management_sdk.resources.v4.shovels.schemas.common import AckMode, DeleteAfter
from rabbitmq_management_sdk.resources.v4.shovels.schemas.shovel_request import (
    Amqp091ShovelDestination,
    Amqp091ShovelSource,
    Amqp10ShovelDestination,
    Amqp10ShovelSource,
    LocalShovelDestination,
    LocalShovelSource,
    ShovelRequest,
    UpsertShovelRequest,
)
from rabbitmq_management_sdk.resources.v4.shovels.schemas.shovel_response import (
    ShovelParameterResponse,
    ShovelState,
    ShovelStatusResponse,
)

__all__ = [
    "APIError",
    "AckMode",
    "Amqp091ShovelDestination",
    "Amqp091ShovelSource",
    "Amqp10ShovelDestination",
    "Amqp10ShovelSource",
    "BadRequestError",
    "BindingDestinationType",
    "BindingRequest",
    "BindingResponse",
    "ClassicQueueRequest",
    "ClusterAuditor",
    "Config",
    "ConflictError",
    "ConnectionError",
    "DeadLetterStrategy",
    "DeleteAfter",
    "ExchangeArguments",
    "ExchangeRequest",
    "ExchangeResponse",
    "ExchangeType",
    "ForbiddenError",
    "LocalShovelDestination",
    "LocalShovelSource",
    "MalformedResponseError",
    "MethodNotAllowedError",
    "NotFoundError",
    "OperatorPolicyApplyTo",
    "OperatorPolicyRequest",
    "OperatorPolicyResponse",
    "OverflowBehaviour",
    "Page",
    "PolicyApplyTo",
    "PolicyDefinition",
    "PolicyDefinitionResponse",
    "PolicyRequest",
    "PolicyResponse",
    "PreconditionFailedError",
    "QueueDeleteOptions",
    "QueueLeaderLocator",
    "QueueRequest",
    "QueueResponse",
    "QuorumQueueOverflow",
    "QuorumQueueRequest",
    "RabbitMQClient",
    "RabbitMQError",
    "RabbitMQVersion",
    "SSLConfig",
    "ServerError",
    "ServiceUnavailableError",
    "ShovelParameterResponse",
    "ShovelRequest",
    "ShovelState",
    "ShovelStatusResponse",
    "StreamQueueRequest",
    "TimeoutError",
    "TooManyRequestsError",
    "TopologyAnalysisError",
    "TopologyAuditReport",
    "TopologyDefinitionsError",
    "TopologyError",
    "TopologyLoadError",
    "TopologyParseError",
    "TopologyResourceSnapshotError",
    "TopologyValidationError",
    "TransportError",
    "UnauthorizedError",
    "UnprocessableEntityError",
    "UpsertShovelRequest",
    "VhostLimitName",
    "VhostLimitRequest",
    "VhostLimitResponse",
    "VhostLimitValues",
    "VhostRequest",
    "VhostResponse",
]
