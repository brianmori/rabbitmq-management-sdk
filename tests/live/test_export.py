import pytest

from rabbitmq_management_sdk import RabbitMQClient, VhostRequest


@pytest.mark.live
def test_global_export(rabbitmq_client_compatibility: RabbitMQClient) -> None:
    admin_service = rabbitmq_client_compatibility.admin
    vhost_name = "test-vh-export"

    # Create a new vhost
    vhost_request = VhostRequest(description="Test Vhost", tags=["test"])
    admin_service.create_vhost(vhost_name, vhost_request)

    resp_all = admin_service.export_definitions()

    assert any(v.name == vhost_name for v in resp_all.vhosts)
