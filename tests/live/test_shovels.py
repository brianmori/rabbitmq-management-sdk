from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

import pytest
from tests.shared.constants import VhostTest

from rabbitmq_management_sdk.domains.v4.queues.schemas.queue_request import QueueRequest, QuorumQueueRequest
from rabbitmq_management_sdk.domains.v4.shovels.schemas.shovel_request import (
    Amqp091ShovelDestination,
    Amqp091ShovelSource,
    Amqp10ShovelDestination,
    Amqp10ShovelSource,
    ShovelRequest,
)
from rabbitmq_management_sdk.exceptions import RabbitMQError

if TYPE_CHECKING:
    from rabbitmq_management_sdk import RabbitMQClient


@pytest.mark.live
def test_create_destroy_shovel_amqp091(
    rabbitmq_client_strict_vhost_src: RabbitMQClient, rabbitmq_client_strict_vhost_dest: RabbitMQClient
) -> None:

    shovel_name = "test.shovel.091"
    vhost_src_name = VhostTest.SRC
    vhost_dst_name = VhostTest.DST
    queue_src_name = "src.queue.091"
    queue_dst_name = "dst.queue.091"

    try:
        # Create queues in the source and destination vhosts
        queue_req = QueueRequest(durable=True, auto_delete=False, arguments=QuorumQueueRequest())
        rabbitmq_client_strict_vhost_src.queues.create(queue_src_name, queue_req)
        rabbitmq_client_strict_vhost_dest.queues.create(queue_dst_name, queue_req)

        queue = rabbitmq_client_strict_vhost_src.queues.get(queue_src_name)
        assert queue.state == "running"

        queue = rabbitmq_client_strict_vhost_dest.queues.get(queue_dst_name)
        assert queue.state == "running"

        shovel_src_uri = f"amqp:///{vhost_src_name}"
        shovel_dst_uri = f"amqp:///{vhost_dst_name}"
        rabbitmq_client_strict_vhost_src.shovels.create(
            name=shovel_name,
            request=ShovelRequest(
                src_uri=shovel_src_uri,
                dest_uri=shovel_dst_uri,
                src_arguments=Amqp091ShovelSource(src_queue=queue_src_name),
                dest_arguments=Amqp091ShovelDestination(dest_queue=queue_dst_name),
            ),
        )

        shovel = rabbitmq_client_strict_vhost_src.shovels.get(shovel_name)
        assert shovel is not None
    finally:
        # Best-effort teardown so a failed assertion does not leak the shovel,
        # queues, or vhosts into subsequent test runs.
        with contextlib.suppress(RabbitMQError):
            rabbitmq_client_strict_vhost_src.shovels.delete(shovel_name)
        with contextlib.suppress(RabbitMQError):
            rabbitmq_client_strict_vhost_dest.queues.delete(queue_dst_name)
        with contextlib.suppress(RabbitMQError):
            rabbitmq_client_strict_vhost_src.queues.delete(queue_src_name)


@pytest.mark.live
def test_create_destroy_shovel_amqp10(
    rabbitmq_client_strict_vhost_src: RabbitMQClient, rabbitmq_client_strict_vhost_dest: RabbitMQClient
) -> None:

    shovel_name = "test.shovel.10"
    vhost_src_name = VhostTest.SRC
    vhost_dst_name = VhostTest.DST
    queue_src_name = "src.queue.10"
    queue_dst_name = "dst.queue.10"

    try:
        # Create queues in the source and destination vhosts
        queue_req = QueueRequest(durable=True, auto_delete=False, arguments=QuorumQueueRequest())
        rabbitmq_client_strict_vhost_src.queues.create(queue_src_name, queue_req)
        rabbitmq_client_strict_vhost_dest.queues.create(queue_dst_name, queue_req)

        queue = rabbitmq_client_strict_vhost_src.queues.get(queue_src_name)
        assert queue.state == "running"

        queue = rabbitmq_client_strict_vhost_dest.queues.get(queue_dst_name)
        assert queue.state == "running"

        shovel_src_uri = f"amqp://lab:lab@localhost:5672?hostname=vhost:{vhost_src_name}&sasl=plain"
        shovel_dst_uri = f"amqp://lab:lab@localhost:5672?hostname=vhost:{vhost_dst_name}&sasl=plain"

        rabbitmq_client_strict_vhost_src.shovels.create(
            name=shovel_name,
            request=ShovelRequest(
                src_uri=shovel_src_uri,
                dest_uri=shovel_dst_uri,
                src_arguments=Amqp10ShovelSource(src_address=f"/queues/{queue_src_name}"),
                dest_arguments=Amqp10ShovelDestination(dest_address=f"/queues/{queue_dst_name}"),
            ),
        )

        shovel = rabbitmq_client_strict_vhost_src.shovels.get(shovel_name)
        assert shovel is not None
    finally:
        # Best-effort teardown so a failed assertion does not leak the shovel,
        # queues, or vhosts into subsequent test runs.
        with contextlib.suppress(RabbitMQError):
            rabbitmq_client_strict_vhost_src.shovels.delete(shovel_name)
        with contextlib.suppress(RabbitMQError):
            rabbitmq_client_strict_vhost_dest.queues.delete(queue_dst_name)
        with contextlib.suppress(RabbitMQError):
            rabbitmq_client_strict_vhost_src.queues.delete(queue_src_name)
