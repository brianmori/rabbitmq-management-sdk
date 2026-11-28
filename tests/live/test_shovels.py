"""Verify saved shovel configuration and message delivery against a broker."""

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
def test_list_shovel_definitions_isolates_vhosts(
    rabbit_config: RabbitSettings,
    vhost_factory: Callable[[VhostRequest | None], str],
) -> None:
    first_vhost, second_vhost = vhost_factory(None), vhost_factory(None)
    # Use a loopback endpoint without an AMQP listener for this configuration-only test.
    uri = "amqp://127.0.0.1:1"
    with (
        RabbitMQClient(rabbit_config.client_config(virtual_host=first_vhost)) as first_client,
        RabbitMQClient(rabbit_config.client_config(virtual_host=second_vhost)) as second_client,
    ):
        for client, dest_queue in ((first_client, "first-archive"), (second_client, "second-archive")):
            client.shovels.create(
                "archive",
                ShovelRequest(
                    src_uri=uri,
                    dest_uri=uri,
                    src_arguments=Amqp091ShovelSource(src_queue="source", src_predeclared=True),
                    dest_arguments=Amqp091ShovelDestination(dest_queue=dest_queue, dest_predeclared=True),
                ),
            )

        for client, vhost, dest_queue in (
            (first_client, first_vhost, "first-archive"),
            (second_client, second_vhost, "second-archive"),
        ):
            (configured,) = client.shovels.list_by_vhost()
            assert (configured.vhost, configured.name) == (vhost, "archive")
            destination = configured.value.dest_arguments
            assert destination.dest_protocol == "amqp091"
            assert destination.dest_queue == dest_queue


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


@pytest.mark.live
def test_amqp10_source_consumer_name_is_configured_and_used_by_link(
    rabbit_config: RabbitSettings,
    live_client: RabbitMQClient,
    live_vhost: str,
    management_api: ManagementAPI,
) -> None:
    version = live_client.version
    if (version.major, version.minor) != (4, 3):
        pytest.skip("shovel source consumer names require RabbitMQ 4.3; 4.4 is deferred")

    live_client.queues.create("source", QueueRequest(arguments=QuorumQueueRequest()))
    live_client.queues.create("destination", QueueRequest(arguments=QuorumQueueRequest()))
    userinfo = f"{quote(rabbit_config.username, safe='')}:{quote(rabbit_config.password, safe='')}"
    consumer_name = "sdk-archive-reader"
    # live_vhost's fixture deletes the shovel and its queues even on failure.
    live_client.shovels.create(
        "named-source",
        ShovelRequest(
            src_uri=f"amqp://{userinfo}@localhost:5672?hostname=vhost:{live_vhost}&sasl=plain",
            dest_uri=f"amqp://{userinfo}@/{live_vhost}",
            src_arguments=Amqp10ShovelSource(src_address="/queues/source", src_consumer_name=consumer_name),
            dest_arguments=Amqp091ShovelDestination(dest_queue="destination", dest_predeclared=True),
        ),
    )
    # The parameters endpoint proves stored configuration, not runtime naming.
    observed = live_client.shovels.get("named-source")
    assert observed.value.src_arguments.src_protocol == "amqp10"
    assert observed.value.src_arguments.src_consumer_name == consumer_name
    wait_for(
        lambda: any(
            status.name == "named-source" and status.state in ("running", "flow")
            for status in live_client.shovels.get_shovel_statuses_by_vhost()
        ),
        "named AMQP 1.0 source shovel to run",
    )

    def named_source_link_is_active() -> bool:
        connections = management_api.request("GET", f"vhosts/{quote(live_vhost, safe='')}/connections")
        assert isinstance(connections, list)
        for connection in connections:
            if connection["protocol"] != "AMQP 1-0":
                continue
            sessions = management_api.request("GET", f"connections/{quote(connection['name'], safe='')}/sessions")
            assert isinstance(sessions, list)
            # Outgoing from the broker means delivery to the shovel's source.
            if any(
                link["link_name"] == consumer_name and link["source_address"] == "/queues/source"
                for session in sessions
                for link in session["outgoing_links"]
            ):
                return True
        return False

    # Session details are runtime evidence, separate from the parameter echo.
    wait_for(named_source_link_is_active, "configured AMQP 1.0 source link name")
    assert management_api.publish(live_vhost, "amq.default", "source", "named-link-delivery-proof")
    management_api.delivered(live_vhost, "destination", "named-link-delivery-proof")
