"""Tests for binding edges and referenced exchange supplements."""

import pytest
from tests.shared.parser_fixtures import _exchange, _queue, _response, _vhost

from rabbitmq_management_sdk.resources.v4.exchanges.schemas.exchange_response import ExchangeResponse
from rabbitmq_management_sdk.topology.models import EdgeKind, NodeKind
from rabbitmq_management_sdk.topology.parser import parse_cluster_topology

pytestmark = pytest.mark.unit


class TestBindingEdges:
    def test_exchange_to_exchange(self) -> None:
        response = _response(
            bindings=[
                {
                    "source": "a",
                    "vhost": "v",
                    "destination": "b",
                    "destination_type": "exchange",
                    "routing_key": "rk",
                    "arguments": {},
                }
            ]
        )
        (edge,) = parse_cluster_topology(response).edges
        assert edge.source.kind == NodeKind.EXCHANGE
        assert edge.target.kind == NodeKind.EXCHANGE
        assert edge.kind == EdgeKind.BINDING
        assert edge.routing_key == "rk"
        assert edge.arguments is None

    def test_exchange_to_queue(self) -> None:
        response = _response(
            bindings=[
                {
                    "source": "a",
                    "vhost": "v",
                    "destination": "q",
                    "destination_type": "queue",
                    "routing_key": "",
                    "arguments": {},
                }
            ]
        )
        (edge,) = parse_cluster_topology(response).edges
        assert edge.target.kind == NodeKind.QUEUE
        assert edge.routing_key == ""  # a real, distinct value -- not "not applicable"

    def test_explicit_null_binding_argument_is_preserved(self) -> None:
        response = _response(
            bindings=[
                {
                    "source": "a",
                    "vhost": "v",
                    "destination": "q",
                    "destination_type": "queue",
                    "routing_key": "",
                    "arguments": {"plugin-option": None},
                }
            ]
        )

        (edge,) = parse_cluster_topology(response).edges

        assert edge.arguments == '{"plugin-option": null}'


class TestReferencedPredeclaredExchanges:
    def test_guaranteed_standard_exchange_is_synthesized(self) -> None:
        response = _response(
            vhosts=[_vhost("v")],
            queues=[_queue("q", "v")],
            bindings=[
                {
                    "source": "amq.topic",
                    "vhost": "v",
                    "destination": "q",
                    "destination_type": "queue",
                    "routing_key": "#",
                    "arguments": {},
                }
            ],
        )

        topology = parse_cluster_topology(response)

        (exchange,) = topology.exchanges
        assert exchange.id.name == "amq.topic"
        assert exchange.exchange_type == "topic"
        assert exchange.durable is True
        assert exchange.internal is False
        assert topology.dangling_edges() == frozenset()

    def test_referenced_default_exchange_is_synthesized(self) -> None:
        response = _response(
            vhosts=[_vhost("v")],
            queues=[
                _queue(
                    "q",
                    "v",
                    **{
                        "x-dead-letter-exchange": "",
                        "x-dead-letter-routing-key": "q",
                    },
                )
            ],
        )

        topology = parse_cluster_topology(response)

        (exchange,) = topology.exchanges
        assert exchange.id.name == ""
        assert exchange.exchange_type == "direct"
        assert topology.dangling_edges() == frozenset()

    def test_observation_supplies_optional_system_exchange(self) -> None:
        response = _response(
            vhosts=[_vhost("v")],
            queues=[_queue("q", "v")],
            bindings=[
                {
                    "source": "amq.rabbitmq.trace",
                    "vhost": "v",
                    "destination": "q",
                    "destination_type": "queue",
                    "routing_key": "#",
                    "arguments": {},
                }
            ],
        )
        observed_exchange = ExchangeResponse.model_validate(
            {
                "name": "amq.rabbitmq.trace",
                "vhost": "v",
                "type": "topic",
                "durable": True,
                "auto_delete": False,
                "internal": True,
                "arguments": {},
            }
        )

        topology = parse_cluster_topology(response, observed_exchanges=[observed_exchange])

        (exchange,) = topology.exchanges
        assert exchange.id.name == "amq.rabbitmq.trace"
        assert exchange.exchange_type == "topic"
        assert exchange.internal is True
        assert topology.dangling_edges() == frozenset()

    def test_declaration_remains_authoritative_when_observation_differs(self) -> None:
        response = _response(
            vhosts=[_vhost("v")],
            queues=[_queue("q", "v")],
            exchanges=[_exchange("events", "v", type_="direct")],
            bindings=[
                {
                    "source": "events",
                    "vhost": "v",
                    "destination": "q",
                    "destination_type": "queue",
                    "routing_key": "",
                    "arguments": {},
                }
            ],
        )
        observed_exchange = ExchangeResponse.model_validate(
            {
                "name": "events",
                "vhost": "v",
                "type": "fanout",
                "durable": False,
                "auto_delete": False,
                "internal": True,
                "arguments": {},
            }
        )

        topology = parse_cluster_topology(response, observed_exchanges=[observed_exchange])

        (exchange,) = topology.exchanges
        assert exchange.exchange_type == "direct"
        assert exchange.durable is True
        assert exchange.internal is False

    def test_unknown_amq_prefixed_exchange_is_not_invented(self) -> None:
        response = _response(
            vhosts=[_vhost("v")],
            queues=[_queue("q", "v")],
            bindings=[
                {
                    "source": "amq.plugin.exchange",
                    "vhost": "v",
                    "destination": "q",
                    "destination_type": "queue",
                    "routing_key": "",
                    "arguments": {},
                }
            ],
        )

        topology = parse_cluster_topology(response)

        assert topology.exchanges == frozenset()
        assert len(topology.dangling_edges()) == 1
