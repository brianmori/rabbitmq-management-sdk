"""Tests for topology node parsing."""

import pytest
from tests.shared.parser_fixtures import _exchange, _queue, _response, _vhost

from rabbitmq_management_sdk import TopologyParseError
from rabbitmq_management_sdk.topology.models import NodeKind
from rabbitmq_management_sdk.topology.parser import parse_cluster_topology

pytestmark = pytest.mark.unit


class TestParseExchange:
    def test_basic_fields(self) -> None:
        response = _response(exchanges=[_exchange("ex1", "v", type_="topic")])
        (node,) = parse_cluster_topology(response).exchanges
        assert node.id.vhost == "v"
        assert node.id.name == "ex1"
        assert node.id.kind == NodeKind.EXCHANGE
        assert node.exchange_type == "topic"

    def test_nonstandard_exchange_type_is_preserved(self) -> None:
        """Confirmed against a real dump: plugin-style exchange types like
        x-local-random are real and must not be rejected."""
        response = _response(exchanges=[_exchange("ex1", "v", type_="x-local-random")])
        (node,) = parse_cluster_topology(response).exchanges
        assert node.exchange_type == "x-local-random"


class TestParseQueue:
    def test_uses_declared_queue_type(self) -> None:
        response = _response(
            vhosts=[_vhost("v", default_queue_type="classic")],
            queues=[_queue("q1", "v", **{"x-queue-type": "quorum"})],
        )
        (node,) = parse_cluster_topology(response).queues
        assert node.queue_type == "quorum"  # declared value wins over vhost default

    def test_falls_back_to_vhost_default_queue_type(self) -> None:
        """x-queue-type is present on every real queue seen so far, but
        isn't required by the wire model -- this exercises the fallback
        path real data doesn't reach."""
        response = _response(
            vhosts=[_vhost("policy", default_queue_type="quorum")],
            queues=[_queue("q1", "policy")],
        )
        (node,) = parse_cluster_topology(response).queues
        assert node.queue_type == "quorum"

    def test_raises_when_type_undeterminable(self) -> None:
        response = _response(queues=[_queue("q1", "nowhere")])
        with pytest.raises(
            TopologyParseError,
            match=r"Queue nowhere/q1 has no x-queue-type.+queue type cannot be determined",
        ) as error:
            parse_cluster_topology(response)

        assert error.value.__cause__ is None
