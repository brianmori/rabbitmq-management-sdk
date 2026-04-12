from typing import TYPE_CHECKING

import pytest

from rabbitmq_management_sdk.domains.v4.queues.schemas.queue_request import ClassicQueueRequest, QueueRequest
from rabbitmq_management_sdk.http_adapter import HttpAdapter, HttpResponse, factory
from rabbitmq_management_sdk.http_adapter.config import BasicAuthentication

if TYPE_CHECKING:
    from conftest import RabbitSettings
    from rabbitmq_management_sdk.client.rabbitmq_client import RabbitMQClient


@pytest.mark.live
def test_basic_rabbitmq_overview(rabbit_config: RabbitSettings) -> None:

    ba: BasicAuthentication = BasicAuthentication(username=rabbit_config.username, password=rabbit_config.password)
    ha: HttpAdapter = factory.create_adapter(
        host=rabbit_config.host, port=rabbit_config.port, default_headers={"Authorization": ba.auth_header}
    )
    hr: HttpResponse = ha.request(method="GET", path="/api/overview")
    assert hr.status_code == 200


# Test to verify connection is not closed after a request
@pytest.mark.live
def test_rabbitmq_overview(rabbitmq_client: RabbitMQClient) -> None:
    version = rabbitmq_client._get_version()
    version = rabbitmq_client._get_version()
    version = rabbitmq_client._get_version()
    assert version.major == 4


# Queue creation and retrieval test
@pytest.mark.live
def test_create_destroy_classic_queue(rabbitmq_client: RabbitMQClient) -> None:
    queue_name = "test_classic_queue"
    queue_req = QueueRequest(durable=True, auto_delete=False, arguments=ClassicQueueRequest())
    rabbitmq_client.queues.create(queue_name, queue_req)
    queue = rabbitmq_client.queues.get(queue_name)
    #    rabbitmq_client.queues.delete(queue_name)
    assert queue.name == queue_name
