"""Tests for RabbitMQ definitions-export response models."""

import pytest

from rabbitmq_management_sdk.resources.v4.admin.schemas.export_response import (
    ClusterExportBinding,
    ClusterExportExchange,
    ClusterExportParameter,
    VhostExportBinding,
    VhostExportExchange,
    VhostExportParameter,
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


@pytest.mark.parametrize("model", [ClusterExportBinding, VhostExportBinding])
@pytest.mark.parametrize("match_mode", ["all", "any", "all-with-x", "any-with-x"])
def test_export_binding_preserves_headers_match_modes(
    model: type[ClusterExportBinding] | type[VhostExportBinding], match_mode: str
) -> None:
    arguments = {"x-match": match_mode, "x-region": "eu", "priority": 5}
    payload: dict[str, object] = {
        "source": "headers",
        "destination": "queue",
        "destination_type": "queue",
        "routing_key": "",
        "arguments": arguments,
    }
    if model is ClusterExportBinding:
        payload["vhost"] = "/"

    binding = model.model_validate(payload)

    assert binding.arguments.model_dump(by_alias=True, exclude_unset=True) == arguments


@pytest.mark.parametrize("model", [ClusterExportParameter, VhostExportParameter])
@pytest.mark.parametrize(
    ("component", "value"),
    [
        ("vhost-limits", []),
        ("vhost-limits", {"max-queues": 5}),
        ("federation-upstream-set", [{"upstream": "east"}, {"upstream": "west", "max-hops": 2}]),
        ("plugin-defined", None),
        ("plugin-defined", "enabled"),
        ("plugin-defined", True),
        ("plugin-defined", 42),
    ],
)
def test_export_parameter_preserves_component_specific_json(
    model: type[ClusterExportParameter] | type[VhostExportParameter], component: str, value: object
) -> None:
    payload: dict[str, object] = {"component": component, "name": "settings", "value": value}
    if model is ClusterExportParameter:
        payload["vhost"] = "/"

    parameter = model.model_validate(payload)

    assert parameter.value == value
    assert parameter.model_dump()["value"] == value
    assert parameter.as_operator_policy() is None
