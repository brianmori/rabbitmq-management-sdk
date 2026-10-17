"""Tests for topology node parsing."""

import pytest
from tests.shared.parser_fixtures import _exchange, _observed_queue, _queue, _response, _vhost

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
    @pytest.mark.parametrize("members, expected", [(None, None), ([], None), (["rabbit@a"], 1), (["a", "b", "c"], 3)])
    def test_replication_factor_uses_captured_membership(self, members: list[str] | None, expected: int | None) -> None:
        response = _response(
            queues=[_queue("q", "v", **{"x-queue-type": "quorum", "x-quorum-initial-group-size": 5})],
        )
        observation = _observed_queue("q", "v", members=members, online=["rabbit@a"])

        (node,) = parse_cluster_topology(response, observed_queues=[observation]).queues

        assert node.replication_factor == expected
        (without_observation,) = parse_cluster_topology(response).queues
        assert without_observation.replication_factor is None

    def test_same_named_queues_use_only_their_own_vhost_observations(self) -> None:
        response = _response(
            vhosts=[_vhost("a", "quorum"), _vhost("b", "quorum")], queues=[_queue("q", v) for v in ("a", "b")]
        )
        topology = parse_cluster_topology(
            response, observed_queues=[_observed_queue("q", "b", members=["a", "b", "c"])]
        )
        assert {node.id.vhost: node.replication_factor for node in topology.queues} == {"a": None, "b": 3}

    @pytest.mark.parametrize("members", [["a", "a"], [""]])
    def test_invalid_quorum_membership_is_rejected(self, members: list[str]) -> None:
        response = _response(queues=[_queue("q", "v", **{"x-queue-type": "quorum"})])
        with pytest.raises(TopologyParseError, match="invalid quorum membership"):
            parse_cluster_topology(response, observed_queues=[_observed_queue("q", "v", members=members)])

    def test_duplicate_observations_are_rejected(self) -> None:
        observation = _observed_queue("q", "v", members=["a"])
        with pytest.raises(TopologyParseError, match="Duplicate queue observations"):
            parse_cluster_topology(_response(), observed_queues=[observation, observation])

    def test_observed_type_cannot_contradict_declared_type(self) -> None:
        response = _response(queues=[_queue("q", "v", **{"x-queue-type": "classic"})])
        with pytest.raises(TopologyParseError, match="inconsistent declared and observed"):
            parse_cluster_topology(response, observed_queues=[_observed_queue("q", "v", members=["a"])])

    @pytest.mark.parametrize("queue_type", ["classic", "stream"])
    def test_other_queue_types_do_not_claim_quorum_replication(self, queue_type: str) -> None:
        response = _response(queues=[_queue("q", "v", **{"x-queue-type": queue_type})])
        observation = _observed_queue("q", "v", type=queue_type, arguments={"x-queue-type": queue_type}, members=["a"])
        (node,) = parse_cluster_topology(response, observed_queues=[observation]).queues
        assert node.replication_factor is None

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
