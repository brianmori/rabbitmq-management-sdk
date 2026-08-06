from __future__ import annotations

import logging
from http import HTTPMethod
from typing import TYPE_CHECKING

from rabbitmq_management_sdk.client.config import RabbitMQMajorVersion, RabbitMQVersion
from rabbitmq_management_sdk.client.utils import create_ssl_context
from rabbitmq_management_sdk.exceptions import MalformedResponseError, RabbitMQError
from rabbitmq_management_sdk.http_adapter import HttpAdapter, HttpResponse, factory
from rabbitmq_management_sdk.http_adapter.config import BasicAuthentication
from rabbitmq_management_sdk.resources.v4.admin.services import AdminManager
from rabbitmq_management_sdk.resources.v4.bindings.services import BindingManager
from rabbitmq_management_sdk.resources.v4.exchanges.services import ExchangeManager
from rabbitmq_management_sdk.resources.v4.policies.services import OperatorPolicyManager, PolicyManager
from rabbitmq_management_sdk.resources.v4.queues.services import QueueManager
from rabbitmq_management_sdk.resources.v4.shovels.services import ShovelManager

if TYPE_CHECKING:
    import ssl

    from rabbitmq_management_sdk.client.config import Config

logger = logging.getLogger(__name__)


class RabbitMQClient:
    """Client for the RabbitMQ Management API.

    Attributes:
      _config: Configuration for the client.
      _ha: HTTP adapter for making API requests.
      _basic_auth: Basic authentication for API requests.
      _version: Semantic version of the RabbitMQ server.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._ha: HttpAdapter
        self._basic_auth: BasicAuthentication

        default_headers: dict[str, str] = {}
        ssl_context: ssl.SSLContext | None = None

        if config.password and config.username:
            self._basic_auth = BasicAuthentication(
                username=config.username, password=config.password.get_secret_value()
            )
            default_headers["Authorization"] = self._basic_auth.auth_header

        if config.ssl_context:
            ssl_context = create_ssl_context(config.ssl_context)

        self._ha = factory.create_adapter(
            host=config.host, port=config.port, default_headers=default_headers, ssl_context=ssl_context
        )

        self._version: RabbitMQVersion = self._get_version()

        logger.debug(
            "RabbitMQClient initialized",
            extra={"host": config.host, "port": config.port, "version": self._version},
        )

    def _get_version(self) -> RabbitMQVersion:
        """Retrieves the RabbitMQ version from the server.

        Returns:
            RabbitMQVersion: The semantic version of the RabbitMQ server.

        Raises:
            RabbitMQError: If there is a problem communicating with the server or parsing the version.
        """
        if self._config.version_override is not None:
            return self._config.version_override

        try:
            hr: HttpResponse = self._ha.request(method=HTTPMethod.GET, path="/api/overview")
            data = hr.json()
        except RabbitMQError as e:
            logger.error(
                "Failed to reach RabbitMQ Management API during version detection. "
                "Consider setting version_override in Config and/or proxy settings.",
                extra={"host": self._config.host, "port": self._config.port},
                exc_info=e,
            )
            raise

        rabbitmq_version = data.get("rabbitmq_version")

        if not isinstance(rabbitmq_version, str):
            logger.error(
                "RabbitMQ version field missing or not a string in /api/overview response. "
                "Consider setting version_override in Config.",
                extra={
                    "host": self._config.host,
                    "port": self._config.port,
                    "rabbitmq_version": rabbitmq_version,
                },
            )
            raise MalformedResponseError(
                f"Expected a string for rabbitmq_version, got {type(rabbitmq_version).__name__}. "
                f"Set version_override in Config to bypass detection."
            )

        try:
            return RabbitMQVersion.parse(rabbitmq_version)
        except ValueError as e:
            logger.error(
                "RabbitMQ version string could not be parsed.",
                extra={
                    "host": self._config.host,
                    "port": self._config.port,
                    "rabbitmq_version": rabbitmq_version,
                },
                exc_info=e,
            )
            raise MalformedResponseError(
                f"Could not parse rabbitmq_version {rabbitmq_version!r}. "
                f"Set version_override in Config to bypass detection."
            ) from e

    @property
    def version(self) -> RabbitMQVersion:
        """The RabbitMQ server version detected at construction (or the configured override)."""
        return self._version

    @property
    def queues(self) -> QueueManager:
        if self._version.major == RabbitMQMajorVersion.V4:
            return QueueManager(http_client=self._ha, vhost=self._config.virtual_host_safe, strict=self._config.strict)
        raise NotImplementedError(f"Version {self._version} not supported")

    @property
    def admin(self) -> AdminManager:
        if self._version.major == RabbitMQMajorVersion.V4:
            return AdminManager(http_client=self._ha, strict=self._config.strict)
        raise NotImplementedError(f"Version {self._version} not supported")

    @property
    def exchanges(self) -> ExchangeManager:
        if self._version.major == RabbitMQMajorVersion.V4:
            return ExchangeManager(
                http_client=self._ha, vhost=self._config.virtual_host_safe, strict=self._config.strict
            )
        raise NotImplementedError(f"Version {self._version} not supported")

    @property
    def bindings(self) -> BindingManager:
        if self._version.major == RabbitMQMajorVersion.V4:
            return BindingManager(
                http_client=self._ha, vhost=self._config.virtual_host_safe, strict=self._config.strict
            )
        raise NotImplementedError(f"Version {self._version} not supported")

    @property
    def shovels(self) -> ShovelManager:
        if self._version.major == RabbitMQMajorVersion.V4:
            return ShovelManager(http_client=self._ha, vhost=self._config.virtual_host_safe, strict=self._config.strict)
        raise NotImplementedError(f"Version {self._version} not supported")

    @property
    def policies(self) -> PolicyManager:
        """Regular policies scoped to the configured virtual host."""
        if self._version.major == RabbitMQMajorVersion.V4:
            return PolicyManager(http_client=self._ha, vhost=self._config.virtual_host_safe, strict=self._config.strict)
        raise NotImplementedError(f"Version {self._version} not supported")

    @property
    def operator_policies(self) -> OperatorPolicyManager:
        """Operator policies scoped to the configured virtual host."""
        if self._version.major == RabbitMQMajorVersion.V4:
            return OperatorPolicyManager(
                http_client=self._ha, vhost=self._config.virtual_host_safe, strict=self._config.strict
            )
        raise NotImplementedError(f"Version {self._version} not supported")
