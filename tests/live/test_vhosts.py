"""Vhost manager round trips without relying on other tests' resources."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from rabbitmq_management_sdk import NotFoundError, VhostLimitName, VhostLimitRequest, VhostRequest

if TYPE_CHECKING:
    from collections.abc import Callable

    from rabbitmq_management_sdk import RabbitMQClient


@pytest.mark.live
def test_vhost_metadata_listing_and_delete(
    rabbitmq_client_compatibility: RabbitMQClient,
    vhost_factory: Callable[[VhostRequest | None], str],
) -> None:
    admin = rabbitmq_client_compatibility.admin
    name = vhost_factory(VhostRequest(description="SDK test vhost", tags=["test"]))
    observed = admin.get_vhost(name)
    assert observed.name == name
    assert observed.description == "SDK test vhost"
    assert observed.tags == ["test"]
    assert name in {vhost.name for vhost in admin.get_all_vhosts()}
    admin.delete_vhost(name)
    assert name not in {vhost.name for vhost in admin.get_all_vhosts()}
    with pytest.raises(NotFoundError):
        admin.get_vhost(name)


@pytest.mark.live
def test_vhost_limits_apply_list_and_clear(rabbitmq_client_compatibility: RabbitMQClient, live_vhost: str) -> None:
    admin = rabbitmq_client_compatibility.admin
    assert admin.get_vhost_limits(live_vhost) is None
    for limit in (VhostLimitName.MAX_CONNECTIONS, VhostLimitName.MAX_QUEUES):
        admin.apply_vhost_limit(live_vhost, limit, VhostLimitRequest(value=3))
    observed = admin.get_vhost_limits(live_vhost)
    assert observed is not None
    assert observed.value.max_connections == observed.value.max_queues == 3
    assert [item for item in admin.get_all_vhosts_limits() if item.vhost == live_vhost] == [observed]
    for limit in (VhostLimitName.MAX_CONNECTIONS, VhostLimitName.MAX_QUEUES):
        admin.delete_vhost_limit(live_vhost, limit)
    assert admin.get_vhost_limits(live_vhost) is None
