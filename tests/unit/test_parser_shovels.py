"""Tests for shovel topology parsing."""

from typing import Literal
from urllib.parse import quote

import pytest
from tests.shared.parser_fixtures import _queue, _response, _shovel_param, _vhost

from rabbitmq_management_sdk import ClusterAuditor
from rabbitmq_management_sdk.exceptions import TopologyParseError
from rabbitmq_management_sdk.topology import EdgeKind, EndpointAuthority
from rabbitmq_management_sdk.topology.models import NodeKind
from rabbitmq_management_sdk.topology.parser import parse_cluster_topology
from rabbitmq_management_sdk.topology.reachability import shovels_with_unconfirmed_endpoints

pytestmark = pytest.mark.unit


class TestShovels:
    @pytest.mark.parametrize("protocol", ["amqp091", "local"])
    @pytest.mark.parametrize("side", ["src", "dest"])
    @pytest.mark.parametrize("key", [None, "", "fixed.key"])
    def test_directional_exchange_keys_reach_endpoint_metadata_and_edges(
        self, protocol: str, side: Literal["src", "dest"], key: str | None
    ) -> None:
        value = {f"{side}-protocol": protocol, f"{side}-uri": "amqp://broker/v", f"{side}-exchange": "events"}
        if key is not None:
            value[f"{side}-exchange-key"] = key
        auditor = ClusterAuditor(
            _response(parameters=[_shovel_param("s", "v", **value)]), in_cluster_amqp_hosts={"broker"}
        )

        (shovel,) = auditor.topology.shovels
        endpoint = shovel.source if side == "src" else shovel.destination
        expected_key = None if side == "src" and key == "" else key
        assert endpoint.resource_name == "events"
        assert endpoint.resource_kind == NodeKind.EXCHANGE
        assert endpoint.routing_key == expected_key
        assert endpoint.vhost == "v"
        assert endpoint.is_confirmed_local is True
        assert endpoint.authorities == (() if protocol == "local" else (EndpointAuthority("amqp", "broker", 5672),))
        (edge,) = auditor.topology.edges
        assert edge.kind == EdgeKind.SHOVEL
        assert edge.routing_key == expected_key
        assert (edge.source.name, edge.target.name) == (("events", "s") if side == "src" else ("s", "events"))
        assert auditor.topology.queues == frozenset()

    @pytest.mark.parametrize(
        ("side", "address", "name", "kind", "key"),
        [
            ("src", "/exchanges/events", None, None, None),
            ("src", "/exchanges/events/", None, None, None),
            ("src", "/exchanges/events/fixed", None, None, None),
            ("src", "/queues/q", "q", NodeKind.QUEUE, None),
            ("dest", "/queues/q", "q", NodeKind.QUEUE, None),
            ("dest", "/exchange/events", "events", NodeKind.EXCHANGE, None),
            ("dest", "/exchange/events/", "events", NodeKind.EXCHANGE, None),
            ("dest", "/exchange/events/fixed", "events", NodeKind.EXCHANGE, "fixed"),
            ("dest", "/exchanges/events", "events", NodeKind.EXCHANGE, ""),
            ("dest", "/exchanges/events/", "events", NodeKind.EXCHANGE, ""),
            ("dest", "/exchanges/events/fixed", "events", NodeKind.EXCHANGE, "fixed"),
        ],
    )
    def test_amqp10_direction_controls_resource_edges(
        self, side: Literal["src", "dest"], address: str, name: str | None, kind: NodeKind | None, key: str | None
    ) -> None:
        value = {
            f"{side}-protocol": "amqp10",
            f"{side}-uri": "amqp://broker?hostname=vhost:v",
            f"{side}-address": address,
        }
        auditor = ClusterAuditor(
            _response(parameters=[_shovel_param("s", "v", **value)]), in_cluster_amqp_hosts={"broker"}
        )

        (shovel,) = auditor.topology.shovels
        endpoint = shovel.source if side == "src" else shovel.destination
        assert endpoint.resource_name == name
        assert endpoint.resource_kind == kind
        assert endpoint.routing_key == key
        assert endpoint.vhost == "v"
        assert endpoint.authorities == (EndpointAuthority("amqp", "broker", 5672),)
        assert endpoint.is_confirmed_local is True
        if name is None:
            assert auditor.topology.edges == frozenset()
        else:
            (edge,) = auditor.topology.edges
            assert edge.routing_key == key
            assert (edge.source.name, edge.target.name) == ((name, "s") if side == "src" else ("s", name))

    @pytest.mark.parametrize("value", [[], None, "invalid", 42])
    def test_non_object_shovel_value_raises_contextual_sdk_error(self, value: object) -> None:
        response = _response(parameters=[{"component": "shovel", "vhost": "v", "name": "bad", "value": value}])

        with pytest.raises(TopologyParseError, match="Shovel 'bad' in vhost 'v' requires a JSON object value"):
            parse_cluster_topology(response)

    def test_amqp091_queue_to_queue(self) -> None:
        """Matches the real 'test-shovel' parameter shape."""
        response = _response(
            parameters=[
                _shovel_param(
                    "test-shovel",
                    "src",
                    **{
                        "src-protocol": "amqp091",
                        "src-uri": "amqp:///src",
                        "src-queue": "src.q",
                        "dest-protocol": "amqp091",
                        "dest-uri": "amqp:///dest",
                        "dest-queue": "dest.q",
                    },
                )
            ]
        )
        topology = parse_cluster_topology(response)
        (shovel,) = topology.shovels
        assert shovel.source.vhost == "src"
        assert shovel.destination.vhost == "dest"
        assert shovel.is_cross_vhost is True
        assert len(topology.edges) == 2

    def test_amqp091_exchange_sourced(self) -> None:
        response = _response(
            parameters=[
                _shovel_param(
                    "s",
                    "a",
                    **{
                        "src-protocol": "amqp091",
                        "src-uri": "amqp:///a",
                        "src-exchange": "events.ex",
                        "src-exchange-key": "order.#",
                        "dest-protocol": "amqp091",
                        "dest-uri": "amqp:///b",
                        "dest-queue": "events.q",
                    },
                )
            ]
        )
        topology = parse_cluster_topology(response)
        src_edge = next(e for e in topology.edges if e.target.kind == NodeKind.SHOVEL)
        assert src_edge.source.kind == NodeKind.EXCHANGE
        assert src_edge.source.name == "events.ex"
        assert src_edge.routing_key == "order.#"

    def test_amqp10_queue_addresses(self) -> None:
        """Matches the real 'test-shovel-10' parameter shape."""
        response = _response(
            parameters=[
                _shovel_param(
                    "test-shovel-10",
                    "test-src",
                    **{
                        "src-protocol": "amqp10",
                        "src-uri": "amqp://lab:lab@localhost:5672?hostname=vhost:test-src&sasl=plain",
                        "src-address": "/queues/src.queue.10",
                        "dest-protocol": "amqp10",
                        "dest-uri": "amqp://lab:lab@localhost:5672?hostname=vhost:test-dst&sasl=plain",
                        "dest-address": "/queues/dst.queue.10",
                    },
                )
            ]
        )
        topology = parse_cluster_topology(response, in_cluster_amqp_hosts={"localhost"})
        (shovel,) = topology.shovels
        assert shovel.source.vhost == "test-src"
        assert shovel.destination.vhost == "test-dst"
        assert shovel.source.resource_name == "src.queue.10"
        assert shovel.source.resource_kind == NodeKind.QUEUE
        assert shovel.source.authorities is not None
        assert shovel.source.authorities[0].scheme == "amqp"
        assert shovel.source.authorities[0].host == "localhost"
        assert shovel.source.authorities[0].port == 5672
        assert "lab" not in repr(shovel.source.authorities)
        assert len(topology.edges) == 2

    def test_amqp10_exchange_and_topic_addresses(self) -> None:
        response = _response(
            parameters=[
                _shovel_param(
                    "s",
                    "a",
                    **{
                        "src-protocol": "amqp10",
                        "src-uri": "amqp://u@h?hostname=vhost:a",
                        "src-address": "/queues/in.q",
                        "dest-protocol": "amqp10",
                        "dest-uri": "amqp://u@h?hostname=vhost:b",
                        "dest-address": "/topic/routing.key.here",
                    },
                )
            ]
        )
        topology = parse_cluster_topology(response, in_cluster_amqp_hosts={"h"})
        dest_edge = next(e for e in topology.edges if e.source.kind == NodeKind.SHOVEL)
        assert dest_edge.target.name == "amq.topic"
        assert dest_edge.routing_key == "routing.key.here"

    def test_unresolvable_vhost_degrades_gracefully(self) -> None:
        """hostname present but not vhost-prefixed -> dest vhost is
        unknown. The ShovelNode is still recorded; only the dest-side
        edge is skipped; is_cross_vhost reports None, not a guess."""
        response = _response(
            parameters=[
                _shovel_param(
                    "s",
                    "a",
                    **{
                        "src-protocol": "amqp10",
                        "src-uri": "amqp://u@h?hostname=vhost:a",
                        "src-address": "/queues/in.q",
                        "dest-protocol": "amqp10",
                        "dest-uri": "amqp://u@h?hostname=some.sni.name",
                        "dest-address": "/queues/out.q",
                    },
                )
            ]
        )
        topology = parse_cluster_topology(response, in_cluster_amqp_hosts={"h"})
        (shovel,) = topology.shovels
        assert shovel.destination.vhost is None
        assert shovel.destination.is_confirmed_local is False
        assert shovel.is_cross_vhost is None
        assert len(topology.edges) == 1  # only the src-side edge was buildable
        assert next(iter(topology.edges)).target.kind == NodeKind.SHOVEL

    def test_mixed_vhost_failover_list_omits_only_the_ambiguous_side(self) -> None:
        """Any URI can win failover, so a mixed-vhost side is unresolved."""
        response = _response(
            parameters=[
                _shovel_param(
                    "s",
                    "declaring-vhost",
                    **{
                        "src-protocol": "amqp091",
                        "src-uri": ["amqp://h/source", "amqp://h/backup"],
                        "src-queue": "in.q",
                        "dest-protocol": "amqp091",
                        "dest-uri": ["amqp://h/destination", "amqp://h/destination"],
                        "dest-queue": "out.q",
                    },
                )
            ]
        )

        topology = parse_cluster_topology(response, in_cluster_amqp_hosts={"h"})

        (shovel,) = topology.shovels
        assert shovel.source.vhost is None
        assert shovel.destination.vhost == "destination"
        assert shovel.is_cross_vhost is None
        assert len(topology.edges) == 1
        (destination_edge,) = topology.edges
        assert destination_edge.source.kind == NodeKind.SHOVEL
        assert destination_edge.target.vhost == "destination"
        assert destination_edge.target.name == "out.q"

    def test_bare_address_format(self) -> None:
        response = _response(
            parameters=[
                _shovel_param(
                    "s",
                    "a",
                    **{
                        "src-protocol": "amqp10",
                        "src-uri": "amqp://u@h?hostname=vhost:a",
                        "src-address": "bare.queue.name",
                        "dest-protocol": "amqp10",
                        "dest-uri": "amqp://u@h?hostname=vhost:b",
                        "dest-address": "/queues/dest.q",
                    },
                )
            ]
        )
        topology = parse_cluster_topology(response, in_cluster_amqp_hosts={"h"})
        src_edge = next(e for e in topology.edges if e.target.kind == NodeKind.SHOVEL)
        assert src_edge.source.name == "bare.queue.name"
        assert src_edge.source.kind == NodeKind.QUEUE

    def test_protocol_defaults_to_amqp091_when_omitted(self) -> None:
        response = _response(
            parameters=[
                _shovel_param(
                    "s",
                    "a",
                    **{"src-uri": "amqp:///a", "src-queue": "q1", "dest-uri": "amqp:///b", "dest-queue": "q2"},
                )
            ]
        )
        topology = parse_cluster_topology(response)
        (shovel,) = topology.shovels
        assert shovel.source.vhost == "a"
        assert len(topology.edges) == 2

    @pytest.mark.parametrize(
        ("protocol", "uri", "confirmed_local"),
        [
            pytest.param("amqp091", "amqp://:5672/v", False, id="hostless-explicit-port"),
            pytest.param("amqp091", "amqps:///v", False, id="hostless-tls"),
            pytest.param("amqp10", "amqp://?hostname=vhost:v", False, id="hostless-amqp10"),
            pytest.param("local", "amqp://:5672/v", False, id="local-hostless-explicit-port"),
            pytest.param("amqp091", ["amqp://broker/v", "amqp://:5672/v"], False, id="invalid-failover-candidate"),
            pytest.param("amqp091", "amqp:///v", True, id="direct"),
            pytest.param("amqp091", ["amqp://broker/v", "amqp:///v"], True, id="valid-failover-candidates"),
        ],
    )
    def test_hostless_shovel_uri_must_support_direct_transport_to_create_loop(
        self, protocol: str, uri: str | list[str], confirmed_local: bool
    ) -> None:
        source_resource = {"src-address": "/queues/q"} if protocol == "amqp10" else {"src-queue": "q"}
        response = _response(
            rabbitmq_version="4.3.8",
            vhosts=[_vhost("v")],
            queues=[_queue("q", "v")],
            parameters=[
                _shovel_param(
                    "relay",
                    "v",
                    **{
                        "src-protocol": protocol,
                        "src-uri": uri,
                        "dest-uri": "amqp:///v",
                        "dest-queue": "q",
                        **source_resource,
                    },
                )
            ],
        )

        auditor = ClusterAuditor(response, in_cluster_amqp_hosts={"broker"})
        (shovel,) = auditor.topology.shovels
        assert shovel.source.is_confirmed_local is confirmed_local
        assert shovel.destination.is_confirmed_local
        assert shovel.source.resource_name == "q"
        assert shovel.source.vhost == "v"
        assert len(auditor.topology.edges) == (2 if confirmed_local else 1)
        assert bool(auditor.message_loop_candidates().cycles) is confirmed_local
        assert auditor.shovels_with_unconfirmed_endpoints() == (() if confirmed_local else (shovel,))

    @pytest.mark.parametrize(
        ("address", "return_queue", "resource_name", "forms_loop"),
        [
            ("/queue/q%20name", "q name", "q%20name", False),
            ("/queues/q%ZZ", "q%ZZ", None, False),
            ("/queues/q%25ZZ", "q%ZZ", "q%ZZ", True),
        ],
    )
    def test_amqp10_address_cannot_create_loop_through_the_wrong_queue(
        self, address: str, return_queue: str, resource_name: str | None, forms_loop: bool
    ) -> None:
        queues = [_queue(return_queue, "v")]
        if resource_name is not None and resource_name != return_queue:
            queues.append(_queue(resource_name, "v"))
        auditor = ClusterAuditor(
            _response(
                vhosts=[_vhost("v")],
                queues=queues,
                parameters=[
                    _shovel_param(
                        "relay",
                        "v",
                        **{
                            "src-protocol": "amqp10",
                            "src-uri": "amqp://broker?hostname=vhost:v",
                            "src-address": address,
                            "dest-uri": "amqp:///v",
                            "dest-queue": return_queue,
                        },
                    )
                ],
            ),
            in_cluster_amqp_hosts={"broker"},
        )

        (shovel,) = auditor.topology.shovels
        assert shovel.source.is_confirmed_local
        assert shovel.source.resource_name == resource_name
        assert {edge.source.name for edge in auditor.topology.edges if edge.target == shovel.id} == (
            set() if resource_name is None else {resource_name}
        )
        assert bool(auditor.message_loop_candidates().cycles) is forms_loop

    @pytest.mark.parametrize(
        ("query_vhost", "source_vhost", "destination_vhost", "forms_loop"),
        [
            pytest.param("team+jobs", "team jobs", "team+jobs", False, id="space"),
            pytest.param("team%2Bjobs", "team+jobs", "team+jobs", True, id="literal-plus"),
            pytest.param("queue%26%2332%3Bspace", "queue space", "queue&#32;space", False, id="decimal-reference"),
        ],
    )
    def test_amqp10_query_decoding_cannot_create_loop_in_the_wrong_vhost(
        self, query_vhost: str, source_vhost: str, destination_vhost: str, forms_loop: bool
    ) -> None:
        vhosts = sorted({source_vhost, destination_vhost})
        auditor = ClusterAuditor(
            _response(
                vhosts=[_vhost(vhost) for vhost in vhosts],
                queues=[_queue("q", vhost) for vhost in vhosts],
                parameters=[
                    _shovel_param(
                        "relay",
                        destination_vhost,
                        **{
                            "src-protocol": "amqp10",
                            "src-uri": f"amqp://broker?hostname=vhost:{query_vhost}",
                            "src-address": "/queues/q",
                            "dest-uri": f"amqp:///{quote(destination_vhost, safe='')}",
                            "dest-queue": "q",
                        },
                    )
                ],
            ),
            in_cluster_amqp_hosts={"broker"},
        )

        (shovel,) = auditor.topology.shovels
        (source_edge,) = (edge for edge in auditor.topology.edges if edge.target == shovel.id)
        assert shovel.source.vhost == source_edge.source.vhost == source_vhost
        assert shovel.destination.vhost == destination_vhost
        assert bool(auditor.message_loop_candidates().cycles) is forms_loop

    @pytest.mark.parametrize(
        ("source_uri", "destination_vhost", "source_vhost", "source_authorities"),
        [
            pytest.param(
                "amqp://brok%65r?hostname=vhost:work",
                "work",
                "work",
                (EndpointAuthority("amqp", "brok%65r", 5672),),
                id="escaped-socket-address",
            ),
            pytest.param("amqp://broker?hostname=vhost:%E2%82%AC", "€", None, None, id="invalid-wire-hostname"),
        ],
    )
    def test_amqp10_uri_cannot_create_loop_without_locality_evidence(
        self,
        source_uri: str,
        destination_vhost: str,
        source_vhost: str | None,
        source_authorities: tuple[EndpointAuthority, ...] | None,
    ) -> None:
        auditor = ClusterAuditor(
            _response(
                vhosts=[_vhost(destination_vhost)],
                queues=[_queue("q", destination_vhost)],
                parameters=[
                    _shovel_param(
                        "relay",
                        destination_vhost,
                        **{
                            "src-protocol": "amqp10",
                            "src-uri": source_uri,
                            "src-address": "/queues/q",
                            "dest-uri": f"amqp:///{quote(destination_vhost, safe='')}",
                            "dest-queue": "q",
                        },
                    )
                ],
            ),
            in_cluster_amqp_hosts={"broker"},
        )

        (shovel,) = auditor.topology.shovels
        assert shovel.source.authorities == source_authorities
        assert shovel.source.vhost == source_vhost
        assert not shovel.source.is_confirmed_local
        (destination_edge,) = auditor.topology.edges
        assert destination_edge.source == shovel.id
        assert destination_edge.target.vhost == destination_vhost
        assert not auditor.message_loop_candidates().cycles

    def test_unconfirmed_endpoints_are_reported_without_merging_with_local_resources(self) -> None:
        """The URI host is required to prove an AMQP endpoint is local.

        The unconfirmed source deliberately shares its vhost and queue name
        with a declared local queue. Treating it as local would fabricate a
        graph edge and could create a false loop.
        """
        response = _response(
            vhosts=[_vhost("v")],
            queues=[_queue("orders", "v", **{"x-queue-type": "classic"})],
            parameters=[
                _shovel_param(
                    "remote-shovel",
                    "v",
                    **{
                        "src-protocol": "amqp091",
                        "src-uri": ["amqp://cluster-node/v", "amqp://remote-source/v"],
                        "src-queue": "orders",
                        "dest-protocol": "amqp091",
                        "dest-uri": ["amqp://cluster-node/v", "amqp://remote-destination/v"],
                        "dest-queue": "orders",
                    },
                )
            ],
        )

        topology = parse_cluster_topology(response, in_cluster_amqp_hosts={"cluster-node"})
        (shovel,) = topology.shovels

        assert topology.edges == frozenset()
        assert shovel.source.is_confirmed_local is False
        assert shovel.destination.is_confirmed_local is False
        assert shovel.source.resource_name == "orders"
        assert shovel.source.resource_kind == NodeKind.QUEUE
        assert shovel.source.authorities is not None
        assert {authority.host for authority in shovel.source.authorities} == {
            "cluster-node",
            "remote-source",
        }
        assert shovels_with_unconfirmed_endpoints(topology) == (shovel,)


class TestLocalProtocolShovels:
    def test_local_protocol_single_uri(self) -> None:
        """Matches the real 'test-shovel-local' parameter shape."""
        response = _response(
            parameters=[
                _shovel_param(
                    "test-shovel-local",
                    "src",
                    **{
                        "src-protocol": "local",
                        "src-uri": "amqp://lab:lab@localhost/src",
                        "src-queue": "src.q",
                        "dest-protocol": "local",
                        "dest-uri": "amqp://lab:lab@localhost/dest",
                        "dest-queue": "dest.q",
                    },
                )
            ]
        )
        topo = parse_cluster_topology(response)
        (shovel,) = topo.shovels
        assert shovel.source.vhost == "src"
        assert shovel.destination.vhost == "dest"

    def test_local_protocol_list_of_uris(self) -> None:
        """Matches the real 'test-shovel-local-multiple' parameter shape."""
        response = _response(
            parameters=[
                _shovel_param(
                    "test-shovel-local-multiple",
                    "src",
                    **{
                        "src-protocol": "local",
                        "src-uri": ["amqp://lab:lab@localhost/src", "amqp://lab:lab@localhost/src"],
                        "src-queue": "src.q",
                        "dest-protocol": "local",
                        "dest-uri": ["amqp://lab:lab@localhost/dest", "amqp://lab:lab@localhost/dest"],
                        "dest-queue": "dest.q",
                    },
                )
            ]
        )
        topo = parse_cluster_topology(response)
        (shovel,) = topo.shovels
        assert shovel.source.vhost == "src"
        assert shovel.destination.vhost == "dest"
