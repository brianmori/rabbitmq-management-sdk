"""Offline quorum majority, placement, and scenario tests."""

import pickle
from dataclasses import FrozenInstanceError

import pytest
from tests.shared.parser_fixtures import _binding, _exchange, _observed_queue, _queue, _response

from rabbitmq_management_sdk import ClusterAuditor, TopologyAnalysisError, TopologyValidationError
from rabbitmq_management_sdk.topology import ClusterTopology, NodeId, NodeKind, QueueNode, QuorumFailureScenario

pytestmark = pytest.mark.unit


def _auditor(**placement: object) -> ClusterAuditor:
    definitions = _response(queues=[_queue("q", "v", **{"x-queue-type": "quorum"})])
    return ClusterAuditor(definitions, queues=[_observed_queue("q", "v", **placement)], exchanges=[])


@pytest.mark.parametrize(("size", "majority", "tolerance"), [(1, 1, 0), (2, 2, 0), (3, 2, 1), (4, 3, 1), (5, 3, 2)])
def test_group_size_and_member_failure_arithmetic(size: int, majority: int, tolerance: int) -> None:
    members = tuple(f"rabbit@{i}" for i in range(size))
    tolerated = QuorumFailureScenario(failed_nodes=members[:tolerance])
    too_many = QuorumFailureScenario(failed_nodes=members[: tolerance + 1])
    report = _auditor(members=members, leader=members[-1], online=members).quorum_resilience(
        scenarios=[too_many, tolerated]
    )

    (queue,) = report.queues
    assert queue.required_majority == majority
    assert queue.theoretical_failure_tolerance == tolerance
    assert queue.observed_failure_margin == tolerance
    assert queue.observed_online_majority is True
    for scenario, expected, margin in ((tolerated, True, 0), (too_many, False, -1)):
        result = next(item for item in report.scenarios if item.scenario == scenario)
        (impact,) = result.queues
        assert impact.failed_members == scenario.failed_nodes
        assert impact.member_majority_after_failure is expected
        assert impact.observed_online_majority_after_failure is expected
        assert impact.observed_failure_margin_after_failure == margin


@pytest.mark.parametrize(
    ("members", "online", "majority", "margin", "online_majority"),
    [
        (["a", "b", "c"], None, 2, None, None),
        (["a", "b", "c"], [], 2, -2, False),
        (["a", "b", "c"], ["b", "c"], 2, 0, True),
        (["a", "b", "c"], ["b"], 2, -1, False),
        (None, ["b", "c"], None, None, None),
        ([], [], None, None, None),
    ],
)
def test_partial_observations_distinguish_unknown_and_minority(
    members: list[str] | None,
    online: list[str] | None,
    majority: int | None,
    margin: int | None,
    online_majority: bool | None,
) -> None:
    report = _auditor(members=members, online=online).quorum_resilience(scenarios=[QuorumFailureScenario()])
    (queue,) = report.queues
    assert queue.required_majority == majority
    assert queue.observed_failure_margin == margin
    assert queue.observed_online_majority is online_majority
    assert queue.queue.quorum_placement is not None
    assert queue.queue.quorum_placement.members == (tuple(members) if members is not None else None)
    impact = report.scenarios[0].queues[0]
    assert impact.affected is False
    assert impact.observed_online_majority_after_failure is online_majority


def test_leader_failure_and_zone_loss_are_independent_of_message_routes() -> None:
    definitions = _response(
        queues=[_queue("q", vhost, **{"x-queue-type": "quorum"}) for vhost in ("b", "a")],
        exchanges=[_exchange("entry", "a")],
        bindings=[_binding("entry", "q", vhost="a")],
    )
    observations = [
        _observed_queue("q", "b", members=["rabbit@D", "rabbit@B", "rabbit@C"], leader="rabbit@D"),
        _observed_queue(
            "q", "a", members=["rabbit@C", "rabbit@A", "rabbit@B"], leader="rabbit@A", online=["rabbit@B", "rabbit@A"]
        ),
    ]
    auditor = ClusterAuditor(definitions, queues=observations, exchanges=[])
    definitions_only = ClusterAuditor(definitions)
    assert set(auditor.topology.nodes_by_id) == set(definitions_only.topology.nodes_by_id)
    assert auditor.topology.edges == definitions_only.topology.edges

    leader = QuorumFailureScenario(failed_nodes=("rabbit@A",))
    zone = QuorumFailureScenario(failed_domains=("zone-1",), failed_nodes=("rabbit@A", "rabbit@A"))
    domains = {"rabbit@D": "zone-3", "rabbit@C": "zone-2", "rabbit@B": "zone-1", "rabbit@A": "zone-1"}
    report = auditor.quorum_resilience(failure_domains=domains, scenarios=[zone, leader, zone])
    assert auditor.audit(failure_domains=domains, scenarios=[leader, zone]).quorum_resilience == report
    assert tuple(item.scenario for item in report.scenarios) == (leader, zone)

    qa, qb = (item.queue.id for item in report.queues)
    assert (qa.vhost, qb.vhost) == ("a", "b")
    assert [
        (broker.broker, broker.failure_domain, broker.member_queue_count, broker.leader_queue_count)
        for broker in report.brokers
    ] == [
        ("rabbit@A", "zone-1", 1, 1),
        ("rabbit@B", "zone-1", 2, 0),
        ("rabbit@C", "zone-2", 2, 0),
        ("rabbit@D", "zone-3", 1, 1),
    ]
    assert report.brokers[1].member_queues == (qa, qb)
    assert report.brokers[0].leader_queues == (qa,)

    leader_loss, zone_loss = report.scenarios
    assert leader_loss.affected_queues == (qa,)
    a, b = leader_loss.queues
    assert a.leader_failed is True
    assert a.member_majority_after_failure is True
    assert a.observed_online_majority_after_failure is False
    assert b.affected is b.leader_failed is False
    assert b.observed_online_majority_after_failure is None

    assert zone_loss.affected_queues == (qa, qb)
    a, b = zone_loss.queues
    assert a.failed_members == ("rabbit@A", "rabbit@B")
    assert a.member_majority_after_failure is False
    assert b.failed_members == ("rabbit@B",)
    assert b.member_majority_after_failure is True
    assert b.leader_failed is False

    restored = ClusterAuditor.from_topology(pickle.loads(pickle.dumps(auditor.topology)))
    reordered = ClusterAuditor(definitions, queues=observations[::-1], exchanges=[])
    for equivalent in (restored, reordered):
        assert equivalent.quorum_resilience(failure_domains=domains, scenarios=[zone, leader]) == report
    assert pickle.loads(pickle.dumps(report)) == report
    domains.clear()
    assert report.brokers[0].failure_domain == "zone-1"
    with pytest.raises(FrozenInstanceError):
        report.brokers[0].failure_domain = "other"  # type: ignore[misc]


def test_domain_loss_with_partial_mapping_preserves_unknown_assignments() -> None:
    auditor = _auditor(members=["rabbit@a", "rabbit@b", "rabbit@c"], leader="rabbit@c", online=["rabbit@a", "rabbit@b"])
    scenario = QuorumFailureScenario(failed_domains=("zone-1",))
    result = auditor.quorum_resilience(scenarios=[scenario]).scenarios[0]
    (impact,) = result.queues
    assert result.affected_queues == ()
    assert impact.affected is impact.leader_failed is impact.member_majority_after_failure is None
    assert impact.observed_online_majority_after_failure is None
    assert impact.failed_members == ()
    assert impact.unmapped_members == ("rabbit@a", "rabbit@b", "rabbit@c")

    domains = {"rabbit@a": "zone-1", "rabbit@b": "zone-2"}
    report = auditor.quorum_resilience(failure_domains=domains, scenarios=[scenario])
    assert report.brokers[-1].failure_domain is None
    (impact,) = report.scenarios[0].queues
    assert impact.affected is True
    assert impact.failed_members == ("rabbit@a",)
    assert impact.unmapped_members == ("rabbit@c",)
    assert impact.leader_failed is impact.member_majority_after_failure is None
    assert impact.observed_online_majority_after_failure is False

    combined = QuorumFailureScenario(failed_nodes=("rabbit@c",), failed_domains=("zone-1",))
    (impact,) = auditor.quorum_resilience(failure_domains=domains, scenarios=[combined]).scenarios[0].queues
    assert impact.failed_members == ("rabbit@a", "rabbit@c")
    assert impact.unmapped_members == ()
    assert impact.leader_failed is True
    assert impact.member_majority_after_failure is False


def test_partial_placement_can_report_leader_loss_without_group_size() -> None:
    scenario = QuorumFailureScenario(failed_nodes=("rabbit@b",))
    report = _auditor(leader="rabbit@b", online=["rabbit@a"]).quorum_resilience(scenarios=[scenario])
    (broker,) = report.brokers
    assert broker.member_queue_count == 0
    assert broker.leader_queue_count == 1
    (impact,) = report.scenarios[0].queues
    assert impact.leader_failed is impact.affected is True
    assert impact.failed_members is None
    assert impact.member_majority_after_failure is None
    assert impact.observed_online_majority_after_failure is None


def test_losing_an_already_offline_leader_preserves_the_reported_online_majority() -> None:
    auditor = _auditor(members=["a", "b", "c"], leader="a", online=["b", "c"])
    scenario = QuorumFailureScenario(failed_nodes=("a",))
    report = auditor.quorum_resilience(scenarios=[scenario])

    (impact,) = report.scenarios[0].queues
    assert impact.affected is impact.leader_failed is True
    assert impact.member_majority_after_failure is impact.observed_online_majority_after_failure is True
    assert impact.observed_failure_margin_after_failure == report.queues[0].observed_failure_margin == 0


def test_count_only_topology_can_calculate_theoretical_tolerance() -> None:
    queue = QueueNode(NodeId("v", "q", NodeKind.QUEUE), "quorum", True, replication_factor=5)
    auditor = ClusterAuditor.from_topology(
        ClusterTopology(exchanges=frozenset(), queues=frozenset({queue}), shovels=frozenset(), edges=frozenset())
    )
    report = auditor.quorum_resilience(scenarios=[QuorumFailureScenario(failed_nodes=("rabbit@a",))])
    (result,) = report.queues
    assert (result.required_majority, result.theoretical_failure_tolerance) == (3, 2)
    assert result.observed_online_majority is result.observed_failure_margin is None
    assert report.brokers == ()
    (impact,) = report.scenarios[0].queues
    assert impact.affected is impact.leader_failed is impact.member_majority_after_failure is None


@pytest.mark.parametrize(
    "options",
    [
        {"failure_domains": []},
        {"failure_domains": {"": "zone"}},
        {"failure_domains": {"rabbit@a": None}},
        {"scenarios": [()]},
    ],
)
def test_invalid_analysis_inputs_are_rejected_even_when_no_queues(options: dict[str, object]) -> None:
    with pytest.raises(TopologyAnalysisError):
        ClusterAuditor(_response()).quorum_resilience(**options)  # type: ignore[arg-type]


def test_failure_scenario_rejects_empty_node_name() -> None:
    with pytest.raises(TopologyValidationError, match="failed_nodes"):
        QuorumFailureScenario(failed_nodes=("",))
