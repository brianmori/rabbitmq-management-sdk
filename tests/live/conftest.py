import pytest

from rabbitmq_management_sdk import RabbitMQClient, VhostRequest
from shared.constants import VhostTest


@pytest.fixture(autouse=True)
def test_create_test_vhost(rabbitmq_client_compatibility: RabbitMQClient) -> None:
    vhost_service = rabbitmq_client_compatibility.admin

    # Create a new vhost
    vhost_request = VhostRequest(description="Test Vhost", tags=["test"])
    vhost_service.create_vhost(VhostTest.SRC, vhost_request)
    vhost_service.create_vhost(VhostTest.DST, vhost_request)
