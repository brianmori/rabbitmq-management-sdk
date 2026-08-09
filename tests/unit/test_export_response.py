"""Tests for RabbitMQ definitions-export response models."""

import pytest

from rabbitmq_management_sdk.resources.v4.admin.schemas.export_response import (
    ClusterExportExchange,
    VhostExportExchange,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("model", [ClusterExportExchange, VhostExportExchange])
def test_export_exchange_defaults_internal_when_omitted(
    model: type[ClusterExportExchange] | type[VhostExportExchange],
) -> None:
    payload: dict[str, object] = {
        "name": "events",
        "type": "topic",
        "durable": True,
        "auto_delete": False,
        "arguments": {},
    }
    if model is ClusterExportExchange:
        payload["vhost"] = "/"

    exchange = model.model_validate(payload)

    assert exchange.internal is False
