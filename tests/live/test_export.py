"""A full export retains a vhost created through the SDK."""

import pytest

from rabbitmq_management_sdk import RabbitMQClient


@pytest.mark.live
def test_global_export(rabbitmq_client_compatibility: RabbitMQClient, live_vhost: str) -> None:
    definitions = rabbitmq_client_compatibility.admin.export_definitions()
    assert live_vhost in {vhost.name for vhost in definitions.vhosts}
