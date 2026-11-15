from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from tests.shared.live_broker import wait_for

from rabbitmq_management_sdk import BindingRequest, ExchangeRequest, NotFoundError, RabbitMQClient, VhostRequest
from rabbitmq_management_sdk.resources.v4.queues.schemas.queue_request import (
    ClassicQueueRequest,
    QueueRequest,
    QuorumQueueRequest,
    StreamQueueRequest,
)
from rabbitmq_management_sdk.resources.v4.queues.schemas.queue_response import ClassicQueueResponse, QuorumQueueResponse

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Literal

    from tests.conftest import RabbitSettings
    from tests.shared.live_broker import ManagementAPI


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
    vhost_factory: Callable[[VhostRequest | None], str],
    strict: bool,
    default_queue_type: Literal["classic", "quorum"],
    arguments: ClassicQueueRequest | QuorumQueueRequest | StreamQueueRequest,
) -> None:
    vhost = vhost_factory(VhostRequest(default_queue_type=default_queue_type))
    admin = rabbitmq_client_compatibility.admin
    with RabbitMQClient(rabbit_config.client_config(virtual_host=vhost, strict=strict)) as client:
        queue_name = "typed-queue"
        client.queues.create(queue_name, QueueRequest(arguments=arguments))
        queue = client.queues.get(queue_name)

        assert queue.type == arguments.queue_type
        assert queue.arguments.queue_type == arguments.queue_type
        assert queue.state == "running"
        assert admin.get_vhost(vhost).default_queue_type == default_queue_type


@pytest.mark.live
@pytest.mark.parametrize("queue_type", ["classic", "quorum"])
def test_sdk_dead_letter_resources_route_rejected_message(
    live_client: RabbitMQClient,
    live_vhost: str,
    management_api: ManagementAPI,
    queue_type: str,
) -> None:
    client = live_client
    client.exchanges.create("dead-letter", ExchangeRequest(type="direct"))
    client.queues.create("parking", QueueRequest(arguments=ClassicQueueRequest()))
    arguments = (ClassicQueueRequest if queue_type == "classic" else QuorumQueueRequest)(
        dead_letter_exchange="dead-letter",
        dead_letter_routing_key="poison",
    )
    client.queues.create("work", QueueRequest(arguments=arguments))
    binding = client.bindings.create_exchange_to_queue("dead-letter", "parking", BindingRequest(routing_key="poison"))
    queue = client.queues.get("work")
    assert queue.type == queue_type
    assert isinstance(queue.arguments, (ClassicQueueResponse, QuorumQueueResponse))
    assert queue.arguments.dead_letter_exchange == "dead-letter"
    assert queue.arguments.dead_letter_routing_key == "poison"
    assert client.bindings.list_exchange_to_queue("dead-letter", "parking") == [binding]
    assert management_api.publish(live_vhost, "amq.default", "work", "dead-letter-manager-proof")
    wait_for(
        lambda: management_api.messages(live_vhost, "work", ackmode="reject_requeue_false"), "work queue rejection"
    )
    parked = management_api.delivered(live_vhost, "parking", "dead-letter-manager-proof")
    death = parked["properties"]["headers"]["x-death"][0]
    assert (death["queue"], death["reason"]) == ("work", "rejected")
    client.bindings.delete_exchange_to_queue("dead-letter", "parking", binding.properties_key)
    assert client.bindings.list_exchange_to_queue("dead-letter", "parking") == []
    for name in ("work", "parking"):
        client.queues.delete(name)
        with pytest.raises(NotFoundError):
            client.queues.get(name)
    client.exchanges.delete("dead-letter")
    with pytest.raises(NotFoundError):
        client.exchanges.get("dead-letter")
