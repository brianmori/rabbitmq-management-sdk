"""Tests for shovel topology parsing."""

from typing import Literal

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
