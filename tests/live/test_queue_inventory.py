"""Check inventory against broker exports without writing new captures."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from rabbitmq_management_sdk import ClusterAuditor, RabbitMQClient, VhostRequest
from rabbitmq_management_sdk.resources.v4.queues.schemas.queue_request import (
    ClassicQueueRequest,
    QueueRequest,
    QuorumQueueRequest,
    StreamQueueRequest,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.conftest import RabbitSettings

pytestmark = pytest.mark.live


def test_inventory_preserves_existing_types_after_vhost_default_changes(
    rabbitmq_client_compatibility: RabbitMQClient,
    rabbit_config: RabbitSettings,
    vhost_factory: Callable[[VhostRequest | None], str],
) -> None:
    populated = vhost_factory(VhostRequest(default_queue_type="classic"))
    empty = vhost_factory(VhostRequest(default_queue_type="quorum"))
    with RabbitMQClient(rabbit_config.client_config(virtual_host=populated)) as client:
        for arguments in (ClassicQueueRequest(), QuorumQueueRequest(), StreamQueueRequest()):
            client.queues.create(arguments.queue_type, QueueRequest(arguments=arguments))
    admin = rabbitmq_client_compatibility.admin
    admin.create_vhost(populated, VhostRequest(default_queue_type="quorum"))

    auditor = ClusterAuditor(admin.export_definitions())
    inventory = auditor.queue_type_inventory()
    vhosts = {vhost.vhost: vhost for vhost in inventory.vhosts}

    assert vhosts[populated].default_queue_type == "quorum"
    assert [
        (group.queue_type, group.queue_count, group.queues[0].id.name) for group in vhosts[populated].queue_types
    ] == [("classic", 1, "classic"), ("quorum", 1, "quorum"), ("stream", 1, "stream")]
    assert vhosts[empty].default_queue_type == "quorum"
    assert vhosts[empty].queue_count == 0
    assert vhosts[empty].queue_types == ()
    assert auditor.audit().queue_type_inventory == inventory
