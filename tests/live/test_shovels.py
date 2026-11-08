"""Shovel managers must configure a running worker and deliver a message."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal
from urllib.parse import quote

import pytest
from tests.shared.live_broker import wait_for

from rabbitmq_management_sdk import (
    Amqp091ShovelDestination,
    Amqp091ShovelSource,
    Amqp10ShovelDestination,
    Amqp10ShovelSource,
    NotFoundError,
    QueueRequest,
    QuorumQueueRequest,
    RabbitMQClient,
    ShovelRequest,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.conftest import RabbitSettings
    from tests.shared.live_broker import ManagementAPI

    from rabbitmq_management_sdk import VhostRequest


@pytest.mark.live
@pytest.mark.parametrize("protocol", ["amqp091", "amqp10"])
def test_shovel_manager_runs_delivers_and_deletes(
    rabbit_config: RabbitSettings,
    vhost_factory: Callable[[VhostRequest | None], str],
    management_api: ManagementAPI,
    protocol: Literal["amqp091", "amqp10"],
) -> None:
    source_vhost, destination_vhost = vhost_factory(None), vhost_factory(None)
    userinfo = f"{quote(rabbit_config.username, safe='')}:{quote(rabbit_config.password, safe='')}"
    source: Amqp091ShovelSource | Amqp10ShovelSource
    destination: Amqp091ShovelDestination | Amqp10ShovelDestination
    if protocol == "amqp091":
        source_uri = f"amqp://{userinfo}@/{source_vhost}"
        destination_uri = f"amqp://{userinfo}@/{destination_vhost}"
        source = Amqp091ShovelSource(src_queue="source", src_predeclared=True)
        destination = Amqp091ShovelDestination(dest_queue="destination", dest_predeclared=True)
    else:
        source_uri = f"amqp://{userinfo}@localhost:5672?hostname=vhost:{source_vhost}&sasl=plain"
        destination_uri = f"amqp://{userinfo}@localhost:5672?hostname=vhost:{destination_vhost}&sasl=plain"
        source = Amqp10ShovelSource(src_address="/queues/source")
        destination = Amqp10ShovelDestination(dest_address="/queues/destination")
    with (
        RabbitMQClient(rabbit_config.client_config(virtual_host=source_vhost, strict=True)) as producer,
        RabbitMQClient(rabbit_config.client_config(virtual_host=destination_vhost, strict=True)) as consumer,
    ):
        producer.queues.create("source", QueueRequest(arguments=QuorumQueueRequest()))
        consumer.queues.create("destination", QueueRequest(arguments=QuorumQueueRequest()))
        producer.shovels.create(
            "transfer",
            ShovelRequest(
                src_uri=source_uri,
                dest_uri=destination_uri,
                src_arguments=source,
                dest_arguments=destination,
            ),
        )
        observed = producer.shovels.get("transfer")
        assert observed.name == "transfer"
        assert observed.vhost == source_vhost
        assert observed.value.src_arguments.src_protocol == protocol
        assert observed.value.dest_arguments.dest_protocol == protocol
        wait_for(
            lambda: any(
                status.name == "transfer" and status.state in ("running", "flow")
                for status in producer.shovels.get_shovel_statuses_by_vhost()
            ),
            "SDK-created shovel to run",
        )
        assert management_api.publish(source_vhost, "amq.default", "source", "manager-delivery-proof")
        management_api.delivered(destination_vhost, "destination", "manager-delivery-proof")
        producer.shovels.delete("transfer")
        with pytest.raises(NotFoundError):
            producer.shovels.get("transfer")
