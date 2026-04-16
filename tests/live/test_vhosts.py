from typing import TYPE_CHECKING

import pytest

from rabbitmq_management_sdk.domains.v4.admin.schemas.vhost_request import (
    VhostLimitName,
    VhostLimitRequest,
    VhostRequest,
)

if TYPE_CHECKING:
    from rabbitmq_management_sdk.client.rabbitmq_client import RabbitMQClient
    from rabbitmq_management_sdk.domains.v4.admin.schemas.vhost_response import VhostLimitResponse, VhostResponse


@pytest.mark.live
def test_create_delete_vhost(rabbitmq_client_compatibility: RabbitMQClient) -> None:
    vhost_service = rabbitmq_client_compatibility.admin
    vhost_name = "test-vhost"

    # Create a new vhost
    vhost_request = VhostRequest(description="Test Vhost", tags=["test"])
    vhost_service.create_vhost(vhost_name, vhost_request)

    # Verify the vhost was created
    vhosts_created: list[VhostResponse] = vhost_service.get_all_vhosts()
    assert any(v.name == vhost_name for v in vhosts_created)
    assert len(vhosts_created) > 1
    # Delete the vhost
    vhost_service.delete_vhost(vhost_name)

    # Verify the vhost was deleted
    vhosts_del: list[VhostResponse] = vhost_service.get_all_vhosts()
    assert not any(v.name == vhost_name for v in vhosts_del)


@pytest.mark.live
def test_vhost_desc_tag(rabbitmq_client_compatibility: RabbitMQClient) -> None:
    vhost_service = rabbitmq_client_compatibility.admin
    vhost_name = "test-vhost"
    vhost_service.create_vhost(vhost_name, VhostRequest(description="Test Vhost", tags=["test"]))
    vhost = vhost_service.get_vhost(vhost_name)
    assert vhost.name == vhost_name
    assert vhost.description == "Test Vhost"
    assert vhost.tags == ["test"]


@pytest.mark.live
def test_apply_vhost_limit(rabbitmq_client_compatibility: RabbitMQClient) -> None:
    vhost_service = rabbitmq_client_compatibility.admin
    vhost_name = "test-vhost"
    vhost_service.create_vhost(vhost_name, VhostRequest())

    vlr = VhostLimitRequest(value=3)
    vhost_service.apply_vhost_limit(vhost_name, VhostLimitName.MAX_CONNECTIONS, vlr)
    vhost_service.apply_vhost_limit(vhost_name, VhostLimitName.MAX_QUEUES, vlr)

    vhosts_lim_single: VhostLimitResponse = vhost_service.get_vhost_limits(vhost_name)

    assert vhosts_lim_single.value.max_connections == 3
    assert vhosts_lim_single.value.max_queues == 3

    vhosts_lim_all: list[VhostLimitResponse] = vhost_service.get_all_vhosts_limits()
    assert len(vhosts_lim_all) == 1
    vhost_service.delete_vhost(vhost_name)
