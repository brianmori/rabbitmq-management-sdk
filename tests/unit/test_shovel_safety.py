"""Verify dynamic shovel safety evidence through the public audit facade."""

import pickle

import pytest
from tests.shared.parser_fixtures import _response, _shovel_param

from rabbitmq_management_sdk import ClusterAuditor, TopologyParseError
from rabbitmq_management_sdk.topology import (
    ShovelAckMode,
    ShovelAckSource,
    ShovelAckTiming,
    ShovelSafety,
    ShovelTransport,
)

pytestmark = pytest.mark.unit


def _auditor(
    overrides: dict[str, object] | None = None, *, version: str | None = "4.3.8", hosts: tuple[str, ...] = ()
) -> ClusterAuditor:
    value = {
        "src-uri": "amqp:///v",
        "src-queue": "in",
        "dest-uri": "amqps://remote.example/v",
        "dest-queue": "out",
        **(overrides or {}),
    }
    return ClusterAuditor(
        _response(rabbitmq_version=version, parameters=[_shovel_param("copy", "v", **value)]),
        in_cluster_amqp_hosts=hosts,
    )


def _fact(auditor: ClusterAuditor) -> ShovelSafety:
    (result,) = auditor.shovel_safety()
    return result


@pytest.mark.parametrize(
    "mode,timing",
    [
        (ShovelAckMode.ON_CONFIRM, ShovelAckTiming.AFTER_DESTINATION_CONFIRM),
        (ShovelAckMode.ON_PUBLISH, ShovelAckTiming.AFTER_DESTINATION_PUBLISH),
        (ShovelAckMode.NO_ACK, ShovelAckTiming.AUTOMATIC),
    ],
)
def test_explicit_ack_mode_identifies_source_acknowledgement_timing(
    mode: ShovelAckMode, timing: ShovelAckTiming
) -> None:
    # Explicit acknowledgement evidence survives even without a captured version.
    ack = _fact(_auditor({"ack-mode": mode.value}, version=None)).acknowledgement
    assert (ack.mode, ack.source, ack.timing) == (mode, ShovelAckSource.EXPLICIT, timing)


@pytest.mark.parametrize("version", ["4.2.9", "4.3.8"])
@pytest.mark.parametrize("protocol", ["amqp091", "amqp10", "local"])
def test_omitted_ack_mode_uses_the_verified_dynamic_default(version: str, protocol: str) -> None:
    ack = _fact(_auditor({"src-protocol": protocol}, version=version)).acknowledgement
    assert ack.mode == ShovelAckMode.ON_CONFIRM
    assert ack.source == ShovelAckSource.BROKER_DEFAULT
    assert ack.timing == ShovelAckTiming.AFTER_DESTINATION_CONFIRM


@pytest.mark.parametrize("value", [None, "", "unknown", "ON-CONFIRM", ["on-confirm"], False])
def test_invalid_ack_mode_never_gets_the_omitted_default(value: object) -> None:
    ack = _fact(_auditor({"ack-mode": value})).acknowledgement
    assert ack.mode == ShovelAckMode.UNKNOWN
    assert ack.source == ShovelAckSource.INVALID_VALUE
    assert ack.timing == ShovelAckTiming.UNKNOWN


@pytest.mark.parametrize("version,protocol", [(None, "amqp091"), ("4.4.0", "amqp091"), ("4.3.8", "x-unsupported")])
def test_omitted_ack_mode_needs_supported_version_and_protocol(version: str | None, protocol: str) -> None:
    ack = _fact(_auditor({"src-protocol": protocol}, version=version)).acknowledgement
    assert ack.mode == ShovelAckMode.UNKNOWN
    assert ack.source == ShovelAckSource.UNKNOWN


@pytest.mark.parametrize("version", ["4.2.9", "4.3.8"])
@pytest.mark.parametrize(
    "protocol,uri,expected",
    [
        ("local", "amqps://host.example/v", ShovelTransport.LOCAL),
        ("amqp091", "amqp:///v", ShovelTransport.LOCAL),
        ("amqp091", "amqp://", ShovelTransport.LOCAL),
        ("amqp091", "amqp://:5672/v", ShovelTransport.UNKNOWN),
        ("amqp091", "amqps:///v", ShovelTransport.UNKNOWN),
        ("amqp10", "amqp://?hostname=vhost:v", ShovelTransport.UNKNOWN),
        ("amqp10", "amqps://?hostname=vhost:v", ShovelTransport.UNKNOWN),
        ("amqp10", "amqp://host.example:5671?hostname=vhost:v", ShovelTransport.NETWORK_AMQP),
        ("amqp091", "amqps://host.example:5672/v", ShovelTransport.NETWORK_AMQPS),
    ],
)
def test_candidate_transport_follows_protocol_and_scheme(
    version: str, protocol: str, uri: str, expected: ShovelTransport
) -> None:
    source = _fact(_auditor({"src-protocol": protocol, "src-uri": uri}, version=version)).source_transport
    assert source.candidates == (expected,)
    assert source.kind == expected


def test_locality_mapping_only_changes_edges_and_locality_evidence() -> None:
    outside = _auditor({"src-uri": "amqp://internal.example/v"})
    inside = _auditor({"src-uri": "amqp://internal.example/v"}, hosts=("internal.example",))
    assert _fact(outside) == _fact(inside)
    assert _fact(outside).source_transport.kind == ShovelTransport.NETWORK_AMQP
    assert _fact(outside).destination_transport.kind == ShovelTransport.NETWORK_AMQPS
    assert not outside.topology.edges
    assert len(inside.topology.edges) == 1
    (shovel,) = inside.topology.shovels
    assert shovel.source.is_confirmed_local
    assert not shovel.destination.is_confirmed_local


def test_hostless_transport_requires_version_even_when_locality_is_confirmed() -> None:
    auditor = _auditor(version=None)
    (shovel,) = auditor.topology.shovels
    assert shovel.source.is_confirmed_local
    assert _fact(auditor).source_transport.kind == ShovelTransport.UNKNOWN
    assert _fact(auditor).destination_transport.kind == ShovelTransport.NETWORK_AMQPS


def test_failover_keeps_candidate_order_duplicates_and_known_mixed_transports() -> None:
    auditor = _auditor(
        {
            "dest-uri": [
                "amqps://server.example/v",
                "amqp://server.example/another-vhost",
                "amqp:///v",
                "amqps://server.example/v",
            ]
        }
    )
    result = _fact(auditor)
    assert result.destination_transport.kind == ShovelTransport.MIXED
    assert result.destination_transport.candidates == (
        ShovelTransport.NETWORK_AMQPS,
        ShovelTransport.NETWORK_AMQP,
        ShovelTransport.LOCAL,
        ShovelTransport.NETWORK_AMQPS,
    )
    (shovel,) = auditor.topology.shovels
    assert shovel.destination.vhost is None
    assert not shovel.destination.is_confirmed_local
    assert shovel.destination.authorities is not None


@pytest.mark.parametrize("bad", ["amqps://[invalid/v", "amqp://host/%ZZ", "", 17, {}])
def test_malformed_candidate_stays_unknown_without_discarding_other_transport_facts(bad: object) -> None:
    auditor = _auditor({"dest-uri": [bad, "amqps://remote.example/v"]})
    result = _fact(auditor)
    assert result.destination_transport.candidates == (ShovelTransport.UNKNOWN, ShovelTransport.NETWORK_AMQPS)
    assert result.destination_transport.kind == ShovelTransport.UNKNOWN
    (shovel,) = auditor.topology.shovels
    assert shovel.destination.authorities is None
    assert len(auditor.topology.edges) == 1  # Only the independently valid source hop.


def test_unknown_candidate_does_not_obscure_an_established_mix() -> None:
    auditor = _auditor({"dest-uri": ["amqp://broker/v", None, "amqps://broker/v"]})
    result = _fact(auditor)
    assert result.destination_transport.kind == ShovelTransport.MIXED
    assert result.destination_transport.candidates == (
        ShovelTransport.NETWORK_AMQP,
        ShovelTransport.UNKNOWN,
        ShovelTransport.NETWORK_AMQPS,
    )


def test_graph_and_report_transfer_only_normalized_safety_evidence(caplog: pytest.LogCaptureFixture) -> None:
    secrets = (
        "SENTINEL_USER",
        "SENTINEL_PASSWORD",
        "SENTINEL_QUERY",
        "SENTINEL_CERT",
        "SENTINEL_KEY",
        "SENTINEL_ARG",
        "SENTINEL_ACK",
        "SENTINEL_BAD_URI",
    )
    auditor = _auditor(
        {
            "src-protocol": "local",
            "src-consumer-args": {"custom": "SENTINEL_ARG"},
            "ack-mode": "SENTINEL_ACK",
            "dest-uri": [
                "amqps://SENTINEL_USER:SENTINEL_PASSWORD@tls.example/v"
                "?password=SENTINEL_QUERY&certfile=%2FSENTINEL_CERT&keyfile=%2FSENTINEL_KEY",
                "amqp://SENTINEL_BAD_URI@/v#malformed",
            ],
        }
    )
    result = _fact(auditor)
    report = auditor.audit()
    payload = pickle.dumps(auditor.topology)
    assert report.shovel_safety == (result,)
    assert result.acknowledgement.source == ShovelAckSource.INVALID_VALUE
    assert result.source_transport.kind == ShovelTransport.LOCAL
    assert result.destination_transport.candidates == (ShovelTransport.NETWORK_AMQPS, ShovelTransport.UNKNOWN)
    rebuilt = ClusterAuditor.from_topology(pickle.loads(payload))
    assert rebuilt.shovel_safety() == (result,)
    assert rebuilt.audit() == report
    for secret in secrets:
        assert secret.encode() not in payload
        assert secret.encode() not in pickle.dumps(report)
        assert all(secret not in repr(value) for value in (auditor.topology, result, report))
        assert secret not in caplog.text


def test_non_object_shovel_error_does_not_echo_sensitive_values() -> None:
    response = _response(parameters=[{"component": "shovel", "vhost": "v", "name": "copy", "value": ["SENTINEL"]}])
    with pytest.raises(TopologyParseError, match="requires a JSON object value") as error:
        ClusterAuditor(response)
    assert "SENTINEL" not in repr(error.value)
