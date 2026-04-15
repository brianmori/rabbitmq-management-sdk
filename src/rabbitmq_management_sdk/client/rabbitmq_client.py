import logging
from http import HTTPMethod
from typing import TYPE_CHECKING

from domains.v4.admin.services import AdminManagerV4

from rabbitmq_management_sdk.client.config import RabbitMQMajorVersion, RabbitMQVersion
from rabbitmq_management_sdk.client.utils import create_ssl_context
from rabbitmq_management_sdk.domains.v4.queues.services import QueueManagerV4
from rabbitmq_management_sdk.http_adapter import HttpAdapter, HttpResponse, TransportError, factory
from rabbitmq_management_sdk.http_adapter.config import BasicAuthentication

if TYPE_CHECKING:
    import ssl

    from rabbitmq_management_sdk.client.config import Config

logger = logging.getLogger(__name__)


class RabbitMQClient:
    """Client for the RabbitMQ Management API.

    Attributes:
      config: Configuration for the client.
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
        """
        Retrieves the RabbitMQ version from the server.

        Returns:
            RabbitMQVersion: The semantic version of the RabbitMQ server.

        Raises:
            TransportError: If there is a problem communicating with the server.
            ValueError: If the RabbitMQ version string is not in the expected format.
        """

        if self._config.version_override is not None:
            return self._config.version_override

        try:
            hr: HttpResponse = self._ha.request(method=HTTPMethod.GET, path="/api/overview")
            data = hr.json()
        except TransportError as e:
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
            raise ValueError(
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
            raise

    @property
    def queues(self) -> QueueManagerV4:
        if self._version.major == RabbitMQMajorVersion.V4:
            return QueueManagerV4(
                http_client=self._ha, vhost=self._config.virtual_host_safe, strict=self._config.strict
            )
        raise NotImplementedError(f"Version {self._version} not supported")

    @property
    def admin(self) -> AdminManagerV4:
        if self._version.major == RabbitMQMajorVersion.V4:
            return AdminManagerV4(http_client=self._ha, strict=self._config.strict)
        raise NotImplementedError(f"Version {self._version} not supported")
