from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from rabbitmq_management_sdk import Config, RabbitMQClient, VhostRequest
from rabbitmq_management_sdk.http_adapter import HttpAdapter, HttpResponse, factory
from rabbitmq_management_sdk.http_adapter.config import BasicAuthentication
from rabbitmq_management_sdk.resources.v4.queues.schemas.queue_request import (
    ClassicQueueRequest,
    QueueRequest,
    QuorumQueueRequest,
    StreamQueueRequest,
)

if TYPE_CHECKING:
    from typing import Literal

    from conftest import RabbitSettings


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
def test_rabbitmq_overview(rabbitmq_client_compatibility: RabbitMQClient) -> None:
    version = rabbitmq_client_compatibility._get_version()
    version = rabbitmq_client_compatibility._get_version()
    version = rabbitmq_client_compatibility._get_version()
    assert version.major == 4


@pytest.mark.live
@pytest.mark.parametrize("strict", [False, True], ids=["compatibility", "strict"])
@pytest.mark.parametrize("default_queue_type", ["classic", "quorum"])
@pytest.mark.parametrize(
    "arguments",
    [ClassicQueueRequest(), QuorumQueueRequest(), StreamQueueRequest()],
    ids=["classic", "quorum", "stream"],
)
def test_queue_type_survives_vhost_defaults(
    rabbitmq_client_compatibility: RabbitMQClient,
    rabbit_config: RabbitSettings,
    strict: bool,
    default_queue_type: Literal["classic", "quorum"],
    arguments: ClassicQueueRequest | QuorumQueueRequest | StreamQueueRequest,
) -> None:
    vhost = f"sdk-queue-types-{uuid4().hex}"
    admin = rabbitmq_client_compatibility.admin
    admin.create_vhost(vhost, VhostRequest(default_queue_type=default_queue_type))
    client: RabbitMQClient | None = None
    try:
        client = RabbitMQClient(
            Config(
                host=rabbit_config.host,
                port=rabbit_config.port,
                username=rabbit_config.username,
                password=rabbit_config.password,
                virtual_host=vhost,
                strict=strict,
            )
        )
        queue_name = "typed-queue"
        client.queues.create(queue_name, QueueRequest(arguments=arguments))
        queue = client.queues.get(queue_name)

        assert queue.type == arguments.queue_type
        assert queue.arguments.queue_type == arguments.queue_type
        assert queue.state == "running"
        assert admin.get_vhost(vhost).default_queue_type == default_queue_type
    finally:
        if client is not None:
            client.close()
        admin.delete_vhost(vhost)
