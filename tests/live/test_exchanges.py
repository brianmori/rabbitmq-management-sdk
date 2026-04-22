# Queue creation and retrieval test
from typing import TYPE_CHECKING

import pytest

from rabbitmq_management_sdk.domains.v4.exchanges.schemas.common import ExchangeType
from rabbitmq_management_sdk.domains.v4.exchanges.schemas.exchange_request import ExchangeRequest

if TYPE_CHECKING:
    from rabbitmq_management_sdk.client.rabbitmq_client import RabbitMQClient
    from rabbitmq_management_sdk.domains.v4.exchanges.schemas.exchange_response import ExchangeResponse


@pytest.mark.live
def test_create_destroy_direct_exchange(rabbitmq_client_compatibility: RabbitMQClient) -> None:
    exchange_name = "test_direct_exchange"

    exchange_req = ExchangeRequest(type=ExchangeType.DIRECT, durable=True, auto_delete=False)
    rabbitmq_client_compatibility.exchanges.create(exchange_name, exchange_req)

    exc: ExchangeResponse = rabbitmq_client_compatibility.exchanges.get(exchange_name)
    assert exc.durable
    rabbitmq_client_compatibility.exchanges.delete(exchange_name)


@pytest.mark.live
def test_get_all_exchanges(rabbitmq_client_compatibility: RabbitMQClient) -> None:

    rabbitmq_client_compatibility.exchanges.list_all()
