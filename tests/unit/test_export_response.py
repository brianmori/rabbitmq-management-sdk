"""Tests for RabbitMQ definitions-export response models."""

import pytest
from pydantic import ValidationError

from rabbitmq_management_sdk.resources.v4.admin.schemas.export_response import (
    ClusterDefinitionsResponse,
    ClusterExportBinding,
    ClusterExportExchange,
    ClusterExportGlobalParameter,
    ClusterExportParameter,
    ClusterExportQueue,
    ExportBindingArguments,
    ExportExchangeArguments,
    ExportQueueArguments,
    HeadersMatchMode,
    VhostExportBinding,
    VhostExportExchange,
    VhostExportParameter,
    VhostExportQueue,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "value",
    [None, "marker", 0, 42, 3.5, True, False, {}, {"nested": [None, "value"]}, [], [None, 1, {"enabled": True}]],
)
def test_global_parameter_preserves_json_value(value: object) -> None:
    payload = {"name": "review-marker", "value": value}

    parameter = ClusterExportGlobalParameter.model_validate(payload)

    assert parameter.value == value
    assert type(parameter.value) is type(value)
    assert parameter.model_dump(exclude_unset=True) == payload
    assert ClusterExportGlobalParameter.model_validate_json(parameter.model_dump_json()) == parameter


def test_global_parameter_value_remains_required() -> None:
    with pytest.raises(ValidationError) as error:
        ClusterExportGlobalParameter.model_validate({"name": "review-marker"})

    assert error.value.errors()[0]["loc"] == ("value",)
    assert error.value.errors()[0]["type"] == "missing"


@pytest.mark.parametrize("name", ["review-marker", "cluster_name", "cluster_tags", "internal_cluster_id"])
def test_null_global_parameter_has_no_typed_identity_or_tag_value(name: str) -> None:
    parameter = ClusterExportGlobalParameter(name=name, value=None)

    assert parameter.as_cluster_name() is None
    assert parameter.as_cluster_tags() is None
    assert parameter.as_internal_cluster_id() is None


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
    arguments = {"x-match": match_mode, "x_match": "custom-value", "x-region": "eu", "priority": 5}
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


@pytest.mark.parametrize("model", [ClusterExportBinding, VhostExportBinding])
@pytest.mark.parametrize(
    "arguments",
    [
        {"x_match": "any"},
        {"x_match": "custom-value"},
        {"x_match": None},
        {"x_match": {"nested": [None, {"x-match": "ordinary-header-value"}]}},
        {"x-match": None, "x_match": "custom-value", "nested": {"null": None}},
    ],
)
def test_export_binding_preserves_literal_header_names(
    model: type[ClusterExportBinding] | type[VhostExportBinding], arguments: dict[str, object]
) -> None:
    binding = model.model_validate(
        {
            "source": "headers",
            "vhost": "/",
            "destination": "q",
            "destination_type": "queue",
            "routing_key": "",
            "arguments": arguments,
        }
    )

    assert binding.arguments.x_match is None
    assert binding.arguments.model_dump(by_alias=True, exclude_unset=True) == arguments
    assert model.model_validate_json(binding.model_dump_json(by_alias=True, exclude_unset=True)) == binding


@pytest.mark.parametrize("model", [ClusterExportQueue, VhostExportQueue])
def test_export_queue_preserves_python_spellings_as_extra_arguments(
    model: type[ClusterExportQueue] | type[VhostExportQueue],
) -> None:
    arguments = {
        "queue_type": "plugin-header-value",
        "dead_letter_exchange": {"nested": None},
        "dead_letter_routing_key": 42,
        "max_length": "untyped-header-value",
        "x-queue-type": "classic",
        "x-max-length": 10,
    }
    queue = model.model_validate(
        {"name": "q", "vhost": "/", "durable": True, "auto_delete": False, "arguments": arguments}
    )

    assert queue.arguments.queue_type == "classic"
    assert queue.arguments.dead_letter_exchange is None
    assert queue.arguments.dead_letter_routing_key is None
    assert queue.arguments.max_length == 10
    assert queue.arguments.model_dump(by_alias=True, exclude_unset=True) == arguments


@pytest.mark.parametrize("model", [ClusterExportExchange, VhostExportExchange])
def test_export_exchange_preserves_python_spelling_as_an_extra_argument(
    model: type[ClusterExportExchange] | type[VhostExportExchange],
) -> None:
    arguments = {"alternate_exchange": {"nested": [None]}, "alternate-exchange": "real-ae"}
    exchange = model.model_validate(
        {
            "name": "events",
            "vhost": "/",
            "type": "direct",
            "durable": True,
            "auto_delete": False,
            "arguments": arguments,
        }
    )

    assert exchange.arguments.alternate_exchange == "real-ae"
    assert exchange.arguments.model_dump(by_alias=True, exclude_unset=True) == arguments


@pytest.mark.parametrize(
    ("model", "arguments"),
    [
        (ClusterExportQueue, {"x-queue-type": "invalid"}),
        (ClusterExportQueue, {"x-dead-letter-exchange": {"invalid": "object"}}),
        (ClusterExportExchange, {"alternate-exchange": {"invalid": "object"}}),
        (ClusterExportBinding, {"x-match": "custom-value"}),
    ],
)
def test_export_arguments_still_validate_reserved_wire_keys(
    model: type[ClusterExportQueue] | type[ClusterExportExchange] | type[ClusterExportBinding],
    arguments: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(
            {
                "name": "resource",
                "vhost": "/",
                "type": "direct",
                "durable": True,
                "auto_delete": False,
                "source": "headers",
                "destination": "q",
                "destination_type": "queue",
                "routing_key": "",
                "arguments": arguments,
            }
        )


def test_explicit_argument_models_keep_python_constructor_convenience() -> None:
    queue = ClusterExportQueue(
        name="q",
        vhost="/",
        durable=True,
        auto_delete=False,
        arguments=ExportQueueArguments(dead_letter_exchange="dlx"),
    )
    exchange = ClusterExportExchange(
        name="headers",
        vhost="/",
        type="headers",
        durable=True,
        auto_delete=False,
        arguments=ExportExchangeArguments(alternate_exchange="ae"),
    )
    binding = ClusterExportBinding(
        source="headers",
        vhost="/",
        destination="q",
        destination_type="queue",
        routing_key="",
        arguments=ExportBindingArguments(x_match=HeadersMatchMode.ANY),
    )

    assert queue.arguments.model_dump(by_alias=True, exclude_unset=True) == {"x-dead-letter-exchange": "dlx"}
    assert exchange.arguments.model_dump(by_alias=True, exclude_unset=True) == {"alternate-exchange": "ae"}
    assert binding.arguments.model_dump(by_alias=True, exclude_unset=True) == {"x-match": "any"}
    with pytest.raises(ValidationError, match="frozen"):
        binding.arguments.__setattr__("x_match", HeadersMatchMode.ALL)


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
