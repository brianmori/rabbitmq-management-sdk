"""End-to-end tests for topology graph construction."""

import pytest
from tests.shared.parser_fixtures import _exchange, _queue, _response, _shovel_param, _vhost

from rabbitmq_management_sdk.topology.models import EdgeKind
from rabbitmq_management_sdk.topology.parser import parse_cluster_topology

pytestmark = pytest.mark.unit


class TestParseClusterTopologyEndToEnd:
    def test_scopes_node_identity_to_internal_cluster_id_and_keeps_label_separate(self) -> None:
        response = _response(
            original_cluster_name="name-recorded-by-the-export",
            global_parameters=[
                {"name": "internal_cluster_id", "value": "rabbitmq-cluster-id-primary"},
                {"name": "cluster_name", "value": "broker-provided-name"},
            ],
            queues=[_queue("orders", "v", **{"x-queue-type": "classic"})],
        )

        topology = parse_cluster_topology(response, cluster_label="production-eu-west-1")
        (queue,) = topology.queues

        assert topology.cluster_id == "rabbitmq-cluster-id-primary"
        assert topology.cluster_name == "broker-provided-name"
        assert topology.cluster_label == "production-eu-west-1"
        assert queue.id.cluster_id == topology.cluster_id
        assert str(queue.id) == "queue:rabbitmq-cluster-id-primary:[v]/orders"

    def test_uses_export_cluster_name_as_default_label_without_using_it_as_identity(self) -> None:
        response = _response(
            global_parameters=[
                {"name": "internal_cluster_id", "value": "rabbitmq-cluster-id-primary"},
                {"name": "cluster_name", "value": "broker-provided-name"},
            ],
            exchanges=[_exchange("orders", "v")],
        )

        topology = parse_cluster_topology(response)
        (exchange,) = topology.exchanges

        assert topology.cluster_name == "broker-provided-name"
        assert topology.cluster_label == "broker-provided-name"
        assert exchange.id.cluster_id == "rabbitmq-cluster-id-primary"

    def test_falls_back_to_original_cluster_name_when_global_parameter_is_absent(self) -> None:
        topology = parse_cluster_topology(
            _response(
                original_cluster_name="name-recorded-by-the-export",
                exchanges=[_exchange("orders", "v")],
            )
        )

        assert topology.cluster_name == "name-recorded-by-the-export"
        assert topology.cluster_label == "name-recorded-by-the-export"

    def test_combines_all_edge_kinds_with_no_dangling_edges(self) -> None:
        response = _response(
            vhosts=[_vhost("v")],
            queues=[
                _queue("q1", "v", **{"x-dead-letter-exchange": "dlx"}),
            ],
            exchanges=[
                _exchange("dlx", "v"),
                _exchange("src.ex", "v"),
                _exchange("ae", "v"),
                _exchange("with-ae", "v", **{"alternate-exchange": "ae"}),
            ],
            bindings=[
                {
                    "source": "src.ex",
                    "vhost": "v",
                    "destination": "q1",
                    "destination_type": "queue",
                    "routing_key": "rk",
                    "arguments": {},
                }
            ],
            parameters=[
                _shovel_param(
                    "shovel1",
                    "v",
                    **{
                        "src-protocol": "amqp091",
                        "src-uri": "amqp:///v",
                        "src-queue": "q1",
                        "dest-protocol": "amqp091",
                        "dest-uri": "amqp:///v",
                        "dest-queue": "q1",
                    },
                )
            ],
        )
        topo = parse_cluster_topology(response)

        by_kind: dict[EdgeKind, int] = {}
        for e in topo.edges:
            by_kind.setdefault(e.kind, 0)
            by_kind[e.kind] += 1

        assert by_kind[EdgeKind.BINDING] == 1
        assert by_kind[EdgeKind.DEAD_LETTER] == 1
        assert by_kind[EdgeKind.ALTERNATE_EXCHANGE] == 1
        assert by_kind[EdgeKind.SHOVEL] == 2
        assert topo.dangling_edges() == frozenset()
        assert len(topo.nodes) == len(topo.exchanges) + len(topo.queues) + len(topo.shovels)

    def test_dangling_edge_is_detected_not_silently_dropped(self) -> None:
        """A queue's DLX naming an exchange that was never created --
        RabbitMQ allows this at declare time; messages silently vanish
        until someone notices. Should show up, not disappear."""
        response = _response(
            vhosts=[_vhost("v")],
            queues=[_queue("q1", "v", **{"x-dead-letter-exchange": "never-created", "x-queue-type": "classic"})],
        )
        topo = parse_cluster_topology(response)
        assert len(topo.dangling_edges()) == 1
        (dangling,) = topo.dangling_edges()
        assert dangling.target.name == "never-created"
