"""Tests for RabbitMQ definitions-export response models."""

import pytest
from pydantic import ValidationError

from rabbitmq_management_sdk.resources.v4.admin.schemas.export_response import (
    ClusterDefinitionsResponse,
    ClusterExportBinding,
    ClusterExportExchange,
    ClusterExportParameter,
    VhostExportBinding,
    VhostExportExchange,
    VhostExportParameter,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="empty-object"),
        pytest.param({"unrelated": []}, id="unknown-fields"),
        pytest.param({"rabbitmq_definition_format": "cluster"}, id="metadata-only"),
        pytest.param({"error": "not_authorised", "reason": "fixture"}, id="error-response"),
        pytest.param({"error": "not_authorised", "queues": []}, id="error-with-definitions-section"),
        pytest.param({"rabbitmq_definition_format": "single_virtual_host", "queues": []}, id="single-vhost-format"),
        pytest.param({"original_vhost_name": "/", "queues": []}, id="single-vhost-without-format"),
        pytest.param({"rabbitmq_definition_format": "unknown", "queues": []}, id="unknown-format"),
        pytest.param({"queues": None}, id="null-section"),
        pytest.param({"queues": {}}, id="object-section"),
        pytest.param([], id="array"),
        pytest.param(None, id="null"),
    ],
)
def test_cluster_definitions_reject_invalid_envelopes(payload: object) -> None:
    with pytest.raises(ValidationError):
        ClusterDefinitionsResponse.model_validate(payload)


@pytest.mark.parametrize(
    "section",
    [
        "users",
        "vhosts",
        "permissions",
        "topic_permissions",
        "global_parameters",
        "parameters",
        "policies",
        "queues",
        "exchanges",
        "bindings",
    ],
)
def test_cluster_definitions_accept_sparse_manual_sections(section: str) -> None:
    definitions = ClusterDefinitionsResponse.model_validate({section: [], "future_broker_field": {"enabled": True}})

    assert definitions.model_fields_set == {section}
    assert definitions.rabbitmq_definition_format is None
    assert definitions.queues == []
    assert definitions.exchanges == []


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
