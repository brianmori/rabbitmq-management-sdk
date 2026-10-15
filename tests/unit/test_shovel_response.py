"""Tests for shovel response reshaping and serialization round trips."""

import pytest
from pydantic import ValidationError

from rabbitmq_management_sdk import ShovelParameterResponse

pytestmark = pytest.mark.unit


def _flat_value(src_protocol: str, dest_protocol: str) -> dict[str, object]:
    value: dict[str, object] = {
        "src-uri": ["amqp://source-a/source", "amqp://source-b/source"],
        "dest-uri": "amqp://destination/dest",
        "src-protocol": src_protocol,
        "dest-protocol": dest_protocol,
        "src-prefetch-count": 17,
        "src-consumer-name": "capture-consumer",
        "dest-add-forward-headers": True,
        "dest-add-timestamp-header": True,
        "ack-mode": "on-publish",
        "reconnect-delay": 7,
        "src-delete-after": 42,
    }
    if src_protocol == "amqp10":
        value["src-address"] = "/queues/source.q"
    else:
        value["src-queue"] = "source.q"
        value["src-queue-args"] = {"x-queue-type": "quorum"}
    if dest_protocol == "amqp10":
        value["dest-address"] = "/queues/dest.q"
        value["dest-application-properties"] = {"region": "eu", "priority": 3}
        value["dest-properties"] = {"subject": "orders"}
        value["dest-message-annotations"] = {"forwarded": True}
    else:
        value["dest-queue"] = "dest.q"
        value["dest-publish-properties"] = {"delivery_mode": 2, "headers": {"region": "eu"}}
    return value


def _response(value: dict[str, object]) -> ShovelParameterResponse:
    return ShovelParameterResponse.model_validate(
        {"component": "shovel", "vhost": "/", "name": "capture-shovel", "value": value}
    )


@pytest.mark.parametrize("src_protocol", ["amqp091", "local", "amqp10"])
@pytest.mark.parametrize("dest_protocol", ["amqp091", "local", "amqp10"])
@pytest.mark.parametrize("by_alias", [False, True], ids=["python-names", "wire-aliases"])
def test_shovel_response_round_trip_preserves_both_endpoints(
    src_protocol: str, dest_protocol: str, by_alias: bool
) -> None:
    response = _response(_flat_value(src_protocol, dest_protocol))

    assert response.value.src_arguments.src_protocol == src_protocol
    assert response.value.dest_arguments.dest_protocol == dest_protocol
    assert ShovelParameterResponse.model_validate_json(response.model_dump_json(by_alias=by_alias)) == response
    assert ShovelParameterResponse.model_validate(response.model_dump(by_alias=by_alias)) == response


@pytest.mark.parametrize("side", ["src", "dest"])
def test_shovel_response_preserves_one_nested_endpoint_alongside_a_flat_endpoint(side: str) -> None:
    value = _flat_value("local", "amqp10")
    response = _response(value)
    field = f"{side}_arguments"
    nested = response.value.model_dump(by_alias=True)[field]
    for key in nested:
        value.pop(key, None)
    value[field] = nested

    assert _response(value) == response


def test_shovel_response_accepts_endpoint_model_instances() -> None:
    response = _response(_flat_value("local", "amqp10"))
    data = response.model_dump()
    data["value"]["src_arguments"] = response.value.src_arguments
    data["value"]["dest_arguments"] = response.value.dest_arguments

    assert ShovelParameterResponse.model_validate(data) == response


@pytest.mark.parametrize("side", ["src", "dest"])
@pytest.mark.parametrize(
    "invalid_arguments", [None, {}, {"src-protocol": "unsupported", "dest-protocol": "unsupported"}]
)
def test_shovel_response_does_not_replace_invalid_nested_endpoints(side: str, invalid_arguments: object) -> None:
    value = _flat_value("amqp091", "amqp091")
    value[f"{side}_arguments"] = invalid_arguments

    with pytest.raises(ValidationError):
        _response(value)


def test_shovel_response_defaults_protocols_for_flat_wire_values() -> None:
    response = _response(
        {"src-uri": "amqp:///source", "dest-uri": "amqp:///dest", "src-queue": "source.q", "dest-queue": "dest.q"}
    )

    assert response.value.src_arguments.src_protocol == "amqp091"
    assert response.value.dest_arguments.dest_protocol == "amqp091"
    assert response.value.src_arguments.model_dump(by_alias=True)["src-queue"] == "source.q"
    assert response.value.dest_arguments.model_dump(by_alias=True)["dest-queue"] == "dest.q"
