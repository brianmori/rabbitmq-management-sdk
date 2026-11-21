"""End-to-end tests for topology graph construction."""

import pytest
from tests.shared.parser_fixtures import (
    _binding,
    _exchange,
    _policy,
    _policy_selections,
    _queue,
    _response,
    _shovel_param,
    _vhost,
)

from rabbitmq_management_sdk import ClusterAuditor, DelayedRetryType
from rabbitmq_management_sdk.resources.v4.admin.schemas.export_response import ClusterDefinitionsResponse
from rabbitmq_management_sdk.topology import DeadLetterCoverageStatus, EdgeKind, NodeKind, VhostLimit, VhostLimitStatus
from rabbitmq_management_sdk.topology.parser import parse_cluster_topology

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("retry_type", list(DelayedRetryType))
@pytest.mark.parametrize("source", ["arguments", "policy"])
@pytest.mark.parametrize("has_dlx", [False, True], ids=["no-dlx", "dlx-cycle"])
def test_quorum_retry_and_timeouts_preserve_routes_and_cycle_findings(
    retry_type: DelayedRetryType, source: str, has_dlx: bool
) -> None:
    route_settings: dict[str, object] = (
        {"dead-letter-exchange": "retry", "dead-letter-routing-key": "work"} if has_dlx else {}
    )
    arguments: dict[str, object] = {"x-queue-type": "quorum", "x-delivery-limit": 1}
    if source == "arguments":
        arguments.update({f"x-{key}": value for key, value in route_settings.items()})
    baseline = _response(
        rabbit_version="4.3.6",
        vhosts=[_vhost("v")],
        queues=[_queue("work", "v", **arguments)],
        exchanges=[_exchange("retry", "v")] if has_dlx else [],
        bindings=[_binding("retry", "work", "work", vhost="v")] if has_dlx else [],
        policies=[_policy("retry", "^work$", "quorum_queues", 1, vhost="v", definition=route_settings)]
        if source == "policy"
        else [],
    )
    selections = _policy_selections(
        vhost="v", name="work", kind=NodeKind.QUEUE, policy_name="retry" if source == "policy" else None
    )
    settings = {
        "delayed-retry-type": retry_type.value,
        "delayed-retry-min": 1000,
        "delayed-retry-max": 30_000,
        "consumer-timeout": 120_000,
        "consumer-disconnected-timeout": 60_000,
    }
    configured = baseline.model_dump(by_alias=True)
    if source == "arguments":
        configured["queues"][0]["arguments"].update({f"x-{key}": value for key, value in settings.items()})
    else:
        configured["policies"][0]["definition"].update(settings)
    baseline_topology = parse_cluster_topology(baseline, user_policy_selections=selections)
    topology = parse_cluster_topology(
        ClusterDefinitionsResponse.model_validate(configured), user_policy_selections=selections
    )

    assert topology.all_node_ids == baseline_topology.all_node_ids
    assert topology.edges == baseline_topology.edges
    assert topology.queues != baseline_topology.queues
    report = ClusterAuditor.from_topology(topology).audit()
    baseline_report = ClusterAuditor.from_topology(baseline_topology).audit()
    assert report.structural_cycles == baseline_report.structural_cycles
    assert report.message_loop_candidates == baseline_report.message_loop_candidates
    assert [(item.queue.id, item.status, item.destinations) for item in report.dead_letter_coverage] == [
        (item.queue.id, item.status, item.destinations) for item in baseline_report.dead_letter_coverage
    ]
    assert not report.dangling_edges
    if has_dlx:
        # Neither backoff nor a finite delivery limit proves that repetition stops.
        assert {(edge.source.name, edge.target.name, edge.kind, edge.routing_key) for edge in topology.edges} == {
            ("work", "retry", EdgeKind.DEAD_LETTER, "work"),
            ("retry", "work", EdgeKind.BINDING, "work"),
        }
        assert len(report.structural_cycles.cycles) == 1
        assert report.message_loop_candidates == report.structural_cycles
        assert len(report.message_loop_candidates.cycles[0]) == 2
        assert report.dead_letter_coverage[0].status == DeadLetterCoverageStatus.DESTINATION_POSSIBLE
        assert report.dead_letter_coverage[0].destinations == (next(iter(topology.queues)).id,)
    else:
        # Consumer redelivery is not represented as a queue self-edge.
        assert not topology.edges
        assert not report.structural_cycles.cycles
        assert not report.message_loop_candidates.cycles
        assert report.dead_letter_coverage[0].status == DeadLetterCoverageStatus.NO_DLX


class TestParseClusterTopologyEndToEnd:
    def test_cleared_vhost_limits_do_not_become_shovel_routes(self) -> None:
        response = _response(
            vhosts=[_vhost("v")],
            queues=[_queue("q", "v", **{"x-queue-type": "classic"})],
            parameters=[
                {"component": "vhost-limits", "vhost": "v", "name": "limits", "value": []},
                {
                    "component": "federation-upstream-set",
                    "vhost": "v",
                    "name": "regions",
                    "value": [{"upstream": "east"}, {"upstream": "west"}],
                },
            ],
        )

        topology = parse_cluster_topology(response)

        assert len(topology.queues) == 1
        assert not topology.shovels
        assert not topology.edges
        assert response.parameters[0].value == []
        assert response.parameters[1].value == [{"upstream": "east"}, {"upstream": "west"}]
        (row,) = topology.vhost_permissions.vhosts
        assert row.max_connections == row.max_queues == VhostLimit(VhostLimitStatus.CLEARED)

    @pytest.mark.parametrize(
        ("wire_value", "status", "value"),
        [
            ({}, VhostLimitStatus.CLEARED, None),
            ({"max-queues": 7}, VhostLimitStatus.CAPPED, 7),
            ({"max-queues": 0}, VhostLimitStatus.BLOCKED, 0),
            ({"max-queues": -20}, VhostLimitStatus.UNLIMITED, -20),
            ({"max-connections": 80}, VhostLimitStatus.UNSET, None),
            ({"max-queues": None}, VhostLimitStatus.UNAVAILABLE, None),
            ({"max-queues": True}, VhostLimitStatus.UNAVAILABLE, None),
            ({"max-queues": "100"}, VhostLimitStatus.UNAVAILABLE, None),
            ({"max-queues": 1.0}, VhostLimitStatus.UNAVAILABLE, None),
            (None, VhostLimitStatus.UNAVAILABLE, None),
            ([["max-queues", 7]], VhostLimitStatus.UNAVAILABLE, None),
        ],
    )
    def test_vhost_limit_wire_values(self, wire_value: object, status: VhostLimitStatus, value: int | None) -> None:
        topology = parse_cluster_topology(
            _response(parameters=[{"component": "vhost-limits", "name": "limits", "vhost": "v", "value": wire_value}])
        )

        (row,) = topology.vhost_permissions.vhosts
        assert row.vhost == "v"
        assert not topology.vhosts
        assert row.max_queues == VhostLimit(status, value)

    def test_limit_runtime_scope_and_conflicts(self) -> None:
        matching = {"component": "vhost-limits", "name": "limits", "vhost": "v"}
        parameters = [
            {**matching, "value": {"max-queues": 7, "max-connections": 80}},
            {**matching, "value": {"max-queues": 9, "max-connections": 80}},
            {**matching, "name": "unrelated", "value": {"max-connections": 1}},
            {**matching, "component": "user-limits", "value": {"max-connections": 2}},
        ]
        vhosts = [{**_vhost("v"), "limits": {"max-queues": 3, "max-connections": -1}}]
        topology = parse_cluster_topology(_response(vhosts=vhosts, parameters=parameters))
        reversed_topology = parse_cluster_topology(_response(vhosts=vhosts, parameters=parameters[::-1]))

        assert topology.vhost_permissions == reversed_topology.vhost_permissions
        (row,) = topology.vhost_permissions.vhosts
        assert row.max_queues == VhostLimit(VhostLimitStatus.UNAVAILABLE)
        assert row.max_connections == VhostLimit(VhostLimitStatus.CAPPED, 80)

    def test_uses_embedded_vhost_limits_as_fallback_evidence(self) -> None:
        topology = parse_cluster_topology(
            ClusterDefinitionsResponse.model_validate(
                {
                    "rabbitmq_version": "4.3.6",
                    "vhosts": [
                        {"name": "empty", "limits": {}},
                        {"name": "limited", "limits": {"max-queues": 0, "max-connections": -1}},
                        {"name": "unknown"},
                    ],
                }
            )
        )

        empty, limited, unknown = topology.vhost_permissions.vhosts
        assert empty.max_queues == empty.max_connections == VhostLimit(VhostLimitStatus.UNSET)
        assert limited.max_queues == VhostLimit(VhostLimitStatus.BLOCKED, 0)
        assert limited.max_connections == VhostLimit(VhostLimitStatus.UNLIMITED, -1)
        assert unknown.max_queues == unknown.max_connections == VhostLimit(VhostLimitStatus.UNAVAILABLE)

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
