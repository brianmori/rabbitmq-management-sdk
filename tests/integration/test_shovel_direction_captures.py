"""Directional endpoints agree with sanitized broker binding/delivery evidence."""

import json
from pathlib import Path

import pytest

from rabbitmq_management_sdk import ClusterAuditor
from rabbitmq_management_sdk.topology import NodeKind

pytestmark = pytest.mark.integration
_CAPTURE_ROOT = Path(__file__).parent
_CASES = (
    [
        f"{protocol}-{side}-{key}"
        for protocol in ("amqp091", "local")
        for side in ("source", "destination")
        for key in ("omitted", "empty", "nonempty")
    ]
    + [f"amqp10-source-{key}" for key in ("omitted", "empty", "nonempty")]
    + [
        f"amqp10-destination-{version}-{key}"
        for version in ("legacy", "v2")
        for key in ("omitted", "empty", "nonempty")
    ]
)


@pytest.mark.parametrize("version", ["4.2", "4.3"])
@pytest.mark.parametrize("case_name", _CASES)
def test_directional_shovel_capture(version: str, case_name: str, tmp_path: Path) -> None:
    evidence = json.loads(
        (_CAPTURE_ROOT / f"rmq-{version}" / "shovel-direction-evidence.json").read_text(encoding="utf-8")
    )
    assert evidence["broker_version"].startswith(f"{version}.")
    assert evidence["legacy_address_v1_permitted"] is True
    case = next(case for case in evidence["cases"] if case["name"] == case_name)
    value = case["value"]
    observations = case["observations"]
    definitions_path = tmp_path / "definitions.json"
    definitions_path.write_text(
        json.dumps({"parameters": [{"component": "shovel", "vhost": "captured", "name": case_name, "value": value}]}),
        encoding="utf-8",
    )

    auditor = ClusterAuditor.from_files(definitions_path, in_cluster_amqp_hosts={"localhost"})

    (shovel,) = auditor.topology.shovels
    incoming = [edge for edge in auditor.topology.edges if edge.target == shovel.id]
    outgoing = [edge for edge in auditor.topology.edges if edge.source == shovel.id]
    (destination_edge,) = outgoing
    assert destination_edge.routing_key == shovel.destination.routing_key
    assert destination_edge.target.name == shovel.destination.resource_name
    for endpoint in (shovel.source, shovel.destination):
        assert endpoint.is_confirmed_local is True
        assert endpoint.vhost == "captured"

    if case_name.startswith("amqp10-source-"):
        assert observations["source_address_rejected"] is True
        assert shovel.source.resource_kind is None
        assert shovel.source.resource_name is None
        assert shovel.source.routing_key is None
        assert shovel.source.authorities is not None
        assert incoming == []
    else:
        assert observations["payload_delivered"] == "delivery-proof"
        (source_edge,) = incoming
        assert source_edge.source.name == shovel.source.resource_name
        assert source_edge.routing_key == shovel.source.routing_key
        if "-source-" in case_name:
            generated = not case_name.endswith("nonempty")
            assert observations["binding_key_is_generated_queue"] is generated
            assert observations["binding_key_is_empty"] is False
            assert shovel.source.resource_kind == NodeKind.EXCHANGE
            assert shovel.source.routing_key == (None if generated else "fixed.key")
        else:
            assert shovel.source.resource_kind == NodeKind.QUEUE
            assert shovel.source.routing_key is None
            assert shovel.destination.resource_kind == NodeKind.EXCHANGE
            if case_name.startswith("amqp10-"):
                expected_key = (
                    "fixed.key" if case_name.endswith("nonempty") else (None if "-legacy-" in case_name else "")
                )
                assert observations["message_subject"] == "delivery.subject"
                assert observations["delivered_key"] == ("delivery.subject" if expected_key is None else expected_key)
            else:
                expected_key = value.get("dest-exchange-key")
                assert observations["delivered_key"] == ("input" if expected_key is None else expected_key)
            assert shovel.destination.routing_key == expected_key

    # These evidence files project away credentials rather than storing a full
    # definitions export or a raw runtime failure reason.
    assert "@" not in json.dumps(value)
