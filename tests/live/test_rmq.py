from typing import TYPE_CHECKING

import pytest

from rabbitmq_management_sdk.http_adapter import HttpAdapter, HttpResponse, factory
from rabbitmq_management_sdk.http_adapter.config import BasicAuthentication

if TYPE_CHECKING:
    from conftest import RabbitSettings
    from rabbitmq_client.rabbitmq_client import RabbitMQClient


@pytest.mark.live
def test_basic_rabbitmq_overview(rabbit_config: RabbitSettings) -> None:

    ba: BasicAuthentication = BasicAuthentication(username=rabbit_config.username, password=rabbit_config.password)
    ha: HttpAdapter = factory.create_adapter(
        host=rabbit_config.host, port=rabbit_config.port, default_headers={"Authorization": ba.auth_header}
    )
    hr: HttpResponse = ha.request(method="GET", path="/api/overview")
    assert hr.status_code == 200


@pytest.mark.live
def test_rabbitmq_overview(rabbitmq_client: RabbitMQClient) -> None:
    version = rabbitmq_client.get_version()
    assert version.startswith("4.")
