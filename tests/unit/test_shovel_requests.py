"""Shovel request aliases, protocol selection, and flattened wire values."""

import pytest
from pydantic import ValidationError

from rabbitmq_management_sdk import (
    Amqp10ShovelDestination,
    Amqp10ShovelSource,
    ShovelRequest,
    UpsertShovelRequest,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("by_alias", [False, True], ids=["python-names", "wire-aliases"])
def test_amqp10_consumer_name_survives_construction_and_flattening(by_alias: bool) -> None:
    source = Amqp10ShovelSource(src_address="/queues/source", src_consumer_name="archive-reader")
    wire = {
        "src-protocol": "amqp10",
        "src-address": "/queues/source",
        "src-consumer-name": "archive-reader",
        "src-prefetch-count": 1000,
    }
    assert source.model_dump(by_alias=True, exclude_none=True) == wire
    assert Amqp10ShovelSource(**{"src-address": "/queues/source", "src-consumer-name": "archive-reader"}) == source
    assert Amqp10ShovelSource.model_validate(wire) == source

    # Exercise the containing request's discriminator with nested dictionaries.
    request = ShovelRequest.model_validate(
        {
            "src-uri": "amqp://localhost",
            "dest-uri": "amqp://backup-host",
            "src_arguments": source.model_dump(by_alias=by_alias),
            "dest_arguments": Amqp10ShovelDestination(dest_address="/queues/archive").model_dump(by_alias=by_alias),
        }
    )
    assert isinstance(request.src_arguments, Amqp10ShovelSource)
    assert request.src_arguments.src_consumer_name == "archive-reader"
    value = request.to_api_value()
    assert value["src-protocol"] == "amqp10"
    assert value["src-consumer-name"] == "archive-reader"
    assert "src_arguments" not in value
    assert "src_consumer_name" not in value
    assert UpsertShovelRequest(value=request).to_api_body() == {"value": value}


@pytest.mark.parametrize("explicit_none", [False, True], ids=["unset", "explicit-none"])
def test_amqp10_without_consumer_name_preserves_existing_payload(explicit_none: bool) -> None:
    source = (
        Amqp10ShovelSource(src_address="/queues/source", src_consumer_name=None)
        if explicit_none
        else Amqp10ShovelSource(src_address="/queues/source")
    )
    request = ShovelRequest(
        src_uri="amqp://localhost",
        dest_uri="amqp://backup-host",
        src_arguments=source,
        dest_arguments=Amqp10ShovelDestination(dest_address="/queues/archive"),
    )
    expected = {
        "src-uri": "amqp://localhost",
        "dest-uri": "amqp://backup-host",
        "src-protocol": "amqp10",
        "src-address": "/queues/source",
        "src-prefetch-count": 1000,
        "dest-protocol": "amqp10",
        "dest-address": "/queues/archive",
        "dest-add-forward-headers": False,
        "dest-add-timestamp-header": False,
        "ack-mode": "on-confirm",
        "reconnect-delay": 1,
        "src-delete-after": "never",
    }
    assert source.src_consumer_name is None
    assert request.to_api_value() == expected
    assert UpsertShovelRequest(value=request).to_api_body() == {"value": expected}


@pytest.mark.parametrize("field", ["src_consumer_name", "src_protocol"])
def test_amqp10_source_remains_frozen(field: str) -> None:
    source = Amqp10ShovelSource(src_address="/queues/source", src_consumer_name="archive-reader")

    with pytest.raises(ValidationError, match="frozen"):
        source.__setattr__(field, "replacement")
