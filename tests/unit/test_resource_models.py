"""Request construction and literal response argument boundaries."""

import json
from typing import Any, cast

import pytest
from pydantic import JsonValue, TypeAdapter, ValidationError

from rabbitmq_management_sdk import PolicyApplyTo, PolicyDefinition, PolicyRequest, PolicyResponse
from rabbitmq_management_sdk.resources._argument_map import ResponseArgumentMap
from rabbitmq_management_sdk.resources.v4.admin.schemas.export_response import (
    ClusterDefinitionsResponse,
    ExportBindingArguments,
    ExportExchangeArguments,
    ExportPolicySettings,
    ExportQueueArguments,
    VhostExportLimits,
)
from rabbitmq_management_sdk.resources.v4.exchanges.schemas.exchange_response import ExchangeArgumentsResponse
from rabbitmq_management_sdk.resources.v4.policies.schemas.policy_response import PolicyDefinitionResponse
from rabbitmq_management_sdk.resources.v4.queues.schemas.queue_response import ClassicQueueResponse, QueueResponse

pytestmark = pytest.mark.unit


@pytest.fixture(
    params=[
        (ExportQueueArguments, "x-dead-letter-exchange", "dead_letter_exchange", "dlx"),
        (ExportExchangeArguments, "alternate-exchange", "alternate_exchange", "fallback"),
        (ExportBindingArguments, "x-match", "x_match", "any"),
        (ExchangeArgumentsResponse, "alternate-exchange", "alternate_exchange", "fallback"),
        (ExportPolicySettings, "dead-letter-exchange", "dead_letter_exchange", "dlx"),
        (PolicyDefinitionResponse, "dead-letter-exchange", "dead_letter_exchange", "dlx"),
        (VhostExportLimits, "max-connections", "max_connections", 7),
    ],
    ids=["queue", "exchange", "binding", "observed-exchange", "exported-policy", "observed-policy", "vhost-limits"],
)
def argument_case(request: pytest.FixtureRequest) -> tuple[type[ResponseArgumentMap[Any]], str, str, JsonValue]:
    return cast("tuple[type[ResponseArgumentMap[Any]], str, str, JsonValue]", request.param)


@pytest.mark.parametrize("reserved_value", ["absent", "null", "valid"])
@pytest.mark.parametrize("entry", ["model", "json", "adapter", "constructor"])
def test_captured_argument_maps_preserve_custom_keys_and_explicit_nulls(
    argument_case: tuple[type[ResponseArgumentMap[Any]], str, str, JsonValue], reserved_value: str, entry: str
) -> None:
    model, wire_key, python_name, value = argument_case
    raw: dict[str, JsonValue] = {
        python_name: {"arbitrary": [None, True, 42]},
        "plugin": None,
        "root": {"nested": "data"},
    }
    if reserved_value != "absent":
        raw[wire_key] = None if reserved_value == "null" else value

    if entry == "model":
        arguments = model.model_validate(raw)
    elif entry == "json":
        arguments = model.model_validate_json(json.dumps(raw))
    elif entry == "adapter":
        arguments = TypeAdapter(model).validate_python(raw)
    else:
        arguments = model(raw)

    assert getattr(arguments, python_name) == (value if reserved_value == "valid" else None)
    assert arguments.root == raw
    assert arguments.model_dump(by_alias=True, exclude_unset=True, exclude_defaults=True) == raw
    assert json.loads(arguments.model_dump_json()) == raw
    assert model.model_validate_json(arguments.model_dump_json()) == arguments
    assert model.from_wire(raw) == arguments
    assert arguments.model_dump(include={wire_key}) == ({wire_key: raw[wire_key]} if wire_key in raw else {})
    assert arguments.model_dump(exclude={python_name}) == {key: item for key, item in raw.items() if key != python_name}


def test_explicit_field_factory_translates_python_names_then_validates(
    argument_case: tuple[type[ResponseArgumentMap[Any]], str, str, JsonValue],
) -> None:
    model, wire_key, python_name, value = argument_case

    arguments = model.from_fields(**{python_name: value, "plugin": [None, "custom"]})

    assert getattr(arguments, python_name) == value
    assert arguments.model_dump() == {wire_key: value, "plugin": [None, "custom"]}
    assert model.from_wire(arguments) == arguments
    with pytest.raises(ValidationError, match="frozen"):
        arguments.__setattr__(python_name, "replacement")


def test_captured_argument_maps_validate_reserved_keys_at_ingestion(
    argument_case: tuple[type[ResponseArgumentMap[Any]], str, str, JsonValue],
) -> None:
    model, wire_key, python_name, _ = argument_case

    with pytest.raises(ValidationError):
        model.from_wire({wire_key: {"invalid": "object"}})
    with pytest.raises(ValidationError):
        model.from_fields(**{python_name: {"invalid": "object"}})


def test_explicit_wire_alias_keeps_a_same_named_python_key_as_custom_data(
    argument_case: tuple[type[ResponseArgumentMap[Any]], str, str, JsonValue],
) -> None:
    model, wire_key, python_name, value = argument_case
    fields = {wire_key: value, python_name: {"ordinary": [None]}}

    arguments = model.from_fields(**fields)

    assert getattr(arguments, python_name) == value
    assert arguments.model_dump() == fields


@pytest.mark.parametrize("invalid", [None, [], "text", {"plugin": object()}])
def test_captured_arguments_require_a_json_object(invalid: object) -> None:
    with pytest.raises(ValidationError):
        ExportExchangeArguments.from_wire(invalid)


@pytest.mark.parametrize("by_alias, by_name", [(True, False), (False, True), (True, True)])
@pytest.mark.parametrize("json_mode", [False, True])
def test_validation_name_flags_never_reinterpret_literal_dictionary_keys(
    argument_case: tuple[type[ResponseArgumentMap[Any]], str, str, JsonValue],
    by_alias: bool,
    by_name: bool,
    json_mode: bool,
) -> None:
    model, _, python_name, value = argument_case
    raw = {python_name: value}

    if json_mode:
        arguments = model.model_validate_json(json.dumps(raw), by_alias=by_alias, by_name=by_name)
    else:
        arguments = model.model_validate(raw, by_alias=by_alias, by_name=by_name)

    assert arguments.model_dump() == raw
    assert getattr(arguments, python_name) is None
    assert model(**raw).model_dump() == raw


@pytest.mark.parametrize(
    "model, wire_key",
    [
        (ExportQueueArguments, "x-max-length"),
        (ExportPolicySettings, "max-length"),
        (PolicyDefinitionResponse, "max-length"),
        (VhostExportLimits, "max-connections"),
    ],
)
@pytest.mark.parametrize("entry", ["model", "json", "adapter", "adapter-json"])
def test_strict_validation_is_honored_at_every_entry_point(
    model: type[ResponseArgumentMap[Any]], wire_key: str, entry: str
) -> None:
    raw = {wire_key: "10"}
    adapter = TypeAdapter(model)

    def validate(*, strict: bool) -> ResponseArgumentMap[Any]:
        if entry == "model":
            return model.model_validate(raw, strict=strict)
        if entry == "json":
            return model.model_validate_json(json.dumps(raw), strict=strict)
        if entry == "adapter":
            return adapter.validate_python(raw, strict=strict)
        return adapter.validate_json(json.dumps(raw), strict=strict)

    assert validate(strict=False).model_dump() == {wire_key: 10}
    with pytest.raises(ValidationError) as error:
        validate(strict=True)
    assert error.value.errors()[0]["type"] == "int_type"


@pytest.mark.parametrize("json_mode", [False, True])
def test_strict_validation_flows_through_containing_responses(json_mode: bool) -> None:
    payload = {
        "queues": [
            {"name": "q", "vhost": "/", "durable": True, "auto_delete": False, "arguments": {"x-max-length": "10"}}
        ],
    }

    with pytest.raises(ValidationError) as error:
        if json_mode:
            ClusterDefinitionsResponse.model_validate_json(json.dumps(payload), strict=True)
        else:
            ClusterDefinitionsResponse.model_validate(payload, strict=True)

    assert error.value.errors()[0]["loc"] == ("queues", 0, "arguments", "x-max-length")
    assert error.value.errors()[0]["type"] == "int_type"


def test_existing_argument_instances_are_revalidated(
    argument_case: tuple[type[ResponseArgumentMap[Any]], str, str, JsonValue],
) -> None:
    model, wire_key, _, value = argument_case
    arguments = model.model_validate({wire_key: value})
    # Frozen models still contain mutable dictionaries. Passing an existing
    # value through a validation boundary must check its current contents.
    arguments.root["plugin"] = object()

    with pytest.raises(ValidationError):
        model.model_validate(arguments)
    with pytest.raises(ValidationError):
        model.from_wire(arguments)
    with pytest.raises(ValidationError):
        TypeAdapter(model).validate_python(arguments)


def test_field_factory_validates_unknown_json_entries() -> None:
    with pytest.raises(ValidationError):
        ExportExchangeArguments.from_fields(plugin=object())


@pytest.mark.parametrize("limits", [None, [1], "invalid", {"max-connections": "invalid"}])
def test_vhost_export_limits_reject_invalid_inputs(limits: object) -> None:
    with pytest.raises(ValidationError):
        VhostExportLimits.model_validate(limits)


def test_vhost_export_limits_preserve_empty_and_unknown_limit_evidence() -> None:
    assert VhostExportLimits.model_validate([]).model_dump() == {}
    assert VhostExportLimits().model_dump() == {}
    raw = {"max_connections": 7, "max-queues": 3, "future-limit": None}
    limits = VhostExportLimits.model_validate(raw)

    assert limits.max_connections is None
    assert limits.max_queues == 3
    assert limits.model_dump(by_alias=True, exclude_unset=True) == raw


def test_request_aliases_do_not_define_the_response_input_contract() -> None:
    request = PolicyRequest(
        pattern="^orders$", definition=PolicyDefinition(max_length=100), priority=1, apply_to=PolicyApplyTo.QUEUES
    )
    wire = {"name": "limits", "vhost": "/", **request.model_dump(by_alias=True, exclude_none=True)}

    response = PolicyResponse.model_validate({**wire, "new_broker_metadata": "ignored"})

    assert response.apply_to == "queues"
    assert response.definition.max_length == 100
    assert "new_broker_metadata" not in response.model_dump()
    python_input = {**wire, "apply_to": wire["apply-to"]}
    del python_input["apply-to"]
    with pytest.raises(ValidationError):
        PolicyResponse.model_validate(python_input)
    # Consumers reloading an SDK-produced Python-name dump can opt in.
    assert PolicyResponse.model_validate(response.model_dump(), by_name=True) == response


def test_queue_observations_require_the_wire_queue_type_argument() -> None:
    wire = {
        "name": "q",
        "vhost": "/",
        "type": "classic",
        "durable": True,
        "auto_delete": False,
        "exclusive": False,
        "node": "rabbit@local",
        "state": "running",
        "arguments": {"queue_type": "classic"},
    }

    with pytest.raises(ValidationError):
        QueueResponse.model_validate(wire)
    wire["arguments"] = {"x-queue-type": "classic", "dead_letter_exchange": "ordinary-key"}
    queue = QueueResponse.model_validate(wire)
    assert isinstance(queue.arguments, ClassicQueueResponse)
    assert queue.arguments.dead_letter_exchange is None
