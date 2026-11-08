"""Exchange manager declarations and deletion on an isolated virtual host."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from rabbitmq_management_sdk import ExchangeRequest, NotFoundError

if TYPE_CHECKING:
    from rabbitmq_management_sdk import RabbitMQClient


@pytest.mark.live
@pytest.mark.parametrize("auto_delete", [False, True])
def test_exchange_manager_round_trip_and_delete(live_client: RabbitMQClient, auto_delete: bool) -> None:
    name = "direct-exchange"
    live_client.exchanges.create(name, ExchangeRequest(type="direct", durable=True, auto_delete=auto_delete))
    exchange = live_client.exchanges.get(name)
    assert exchange.name == name
    assert exchange.type == "direct"
    assert exchange.durable is True
    assert exchange.auto_delete is auto_delete
    assert name in {item.name for item in live_client.exchanges.list_by_vhost()}
    live_client.exchanges.delete(name)
    with pytest.raises(NotFoundError):
        live_client.exchanges.get(name)
