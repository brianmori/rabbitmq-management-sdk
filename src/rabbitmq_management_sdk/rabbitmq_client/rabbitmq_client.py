import logging
from typing import TYPE_CHECKING

from rabbitmq_management_sdk.http_adapter import HttpAdapter, HttpResponse, factory
from rabbitmq_management_sdk.http_adapter.config import BasicAuthentication
from rabbitmq_management_sdk.rabbitmq_client.utils import create_ssl_context

if TYPE_CHECKING:
    import ssl

    from rabbitmq_client.config import Config

logger = logging.getLogger(__name__)


class RabbitMQClient:
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

        logger.debug(
            "RabbitMQClient initialized",
            extra={"host": config.host, "port": config.port},
        )

    def get_version(self) -> str:
        with self._ha:
            hr: HttpResponse = self._ha.request(method="GET", path="/api/overview")
            return hr.json()["rabbitmq_version"]
