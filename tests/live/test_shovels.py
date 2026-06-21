from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

import pytest

from rabbitmq_management_sdk.domains.v4.admin.schemas.vhost_request import VhostRequest
from rabbitmq_management_sdk.domains.v4.queues.schemas.queue_request import QueueRequest, QuorumQueueRequest
from rabbitmq_management_sdk.domains.v4.shovels.schemas.shovel_request import (
    Amqp091ShovelDestination,
    Amqp091ShovelSource,
    ShovelRequest,
)
from rabbitmq_management_sdk.exceptions import RabbitMQError

if TYPE_CHECKING:
    from rabbitmq_management_sdk import RabbitMQClient


@pytest.mark.live
def test_create_destroy_shovel(rabbitmq_client_compatibility: RabbitMQClient) -> None:
    vhost_service = rabbitmq_client_compatibility.admin
    vhost_src_name = "t-shovel-src"
    vhost_dst_name = "t-shovel-dst"
    queue_src_name = "src.queue"
    queue_dst_name = "dst.queue"

    try:
        # Create new vhosts
        vhost_request = VhostRequest(description="Test Vhost", tags=["test"])
        vhost_service.create_vhost(name=vhost_src_name, request=vhost_request)
        vhost_service.create_vhost(name=vhost_dst_name, request=vhost_request)

        # Create queues in the source and destination vhosts
        queue_req = QueueRequest(durable=True, auto_delete=False, arguments=QuorumQueueRequest())
        rabbitmq_client_compatibility.queues.create(queue_src_name, queue_req)
        rabbitmq_client_compatibility.queues.create(queue_dst_name, queue_req)

        queue = rabbitmq_client_compatibility.queues.get(queue_src_name)
        assert queue.state == "running"

        queue = rabbitmq_client_compatibility.queues.get(queue_dst_name)
        assert queue.state == "running"

        shovel_src_uri = f"amqp:///{vhost_src_name}"
        shovel_dst_uri = f"amqp:///{vhost_dst_name}"
        rabbitmq_client_compatibility.shovels.create(
            name="test-shovel",
            request=ShovelRequest(
                src_uri=shovel_src_uri,
                dest_uri=shovel_dst_uri,
                src_arguments=Amqp091ShovelSource(src_queue=queue_src_name),
                dest_arguments=Amqp091ShovelDestination(dest_queue=queue_dst_name),
            ),
        )

        shovel = rabbitmq_client_compatibility.shovels.get("test-shovel")
        assert shovel is not None
    finally:
        # Best-effort teardown so a failed assertion does not leak the shovel,
        # queues, or vhosts into subsequent test runs.
        with contextlib.suppress(RabbitMQError):
            rabbitmq_client_compatibility.shovels.delete("test-shovel")
        with contextlib.suppress(RabbitMQError):
            rabbitmq_client_compatibility.queues.delete(queue_dst_name)
        with contextlib.suppress(RabbitMQError):
            rabbitmq_client_compatibility.queues.delete(queue_src_name)
        with contextlib.suppress(RabbitMQError):
            vhost_service.delete_vhost(vhost_src_name)
        with contextlib.suppress(RabbitMQError):
            vhost_service.delete_vhost(vhost_dst_name)

