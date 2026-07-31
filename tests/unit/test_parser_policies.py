"""Tests for policy-derived topology routes."""

import pytest
from tests.shared.parser_fixtures import (
    _exchange,
    _policy,
    _policy_selections,
    _queue,
    _response,
    _shovel_param,
    _vhost,
)

from rabbitmq_management_sdk import TopologyParseError
from rabbitmq_management_sdk.resources.v4.admin.schemas.export_response import ClusterDefinitionsResponse
from rabbitmq_management_sdk.topology.models import NodeId, NodeKind
from rabbitmq_management_sdk.topology.parser import parse_cluster_topology

pytestmark = pytest.mark.unit


def _scoped_policy_response(
    *,
    apply_to: str,
    kind: NodeKind,
    queue_type: str | None,
) -> ClusterDefinitionsResponse:
    """Build one resource with a selected policy-derived route."""
    if kind == NodeKind.EXCHANGE:
        return _response(
            exchanges=[_exchange("resource", "t")],
            policies=[
                _policy(
                    "scoped-route",
                    ".*",
                    apply_to,
                    0,
                    definition={"alternate-exchange": "route-target"},
                )
            ],
        )
    return _response(
        queues=[_queue("resource", "t", **{"x-queue-type": queue_type})],
        policies=[
            _policy(
                "scoped-route",
                ".*",
                apply_to,
                0,
                definition={"dead-letter-exchange": "route-target"},
            )
        ],
    )


class TestPolicyApplyToScopes:
    @pytest.mark.parametrize(
        ("apply_to", "kind", "queue_type"),
        [
            ("all", NodeKind.QUEUE, "quorum"),
            ("all", NodeKind.EXCHANGE, None),
            ("queues", NodeKind.QUEUE, "classic"),
            ("classic_queues", NodeKind.QUEUE, "classic"),
            ("quorum_queues", NodeKind.QUEUE, "quorum"),
            ("streams", NodeKind.QUEUE, "stream"),
            ("exchanges", NodeKind.EXCHANGE, None),
        ],
    )
    def test_selected_policy_applies_to_a_compatible_resource(
        self,
        apply_to: str,
        kind: NodeKind,
        queue_type: str | None,
    ) -> None:
        topology = parse_cluster_topology(
            _scoped_policy_response(
                apply_to=apply_to,
                kind=kind,
                queue_type=queue_type,
            ),
            user_policy_selections=_policy_selections(
                vhost="t",
                name="resource",
                kind=kind,
                policy_name="scoped-route",
            ),
        )

        (edge,) = topology.edges
        assert edge.target.name == "route-target"

    @pytest.mark.parametrize(
        ("apply_to", "kind", "queue_type"),
        [
            ("queues", NodeKind.EXCHANGE, None),
            ("classic_queues", NodeKind.QUEUE, "quorum"),
            ("quorum_queues", NodeKind.QUEUE, "classic"),
            ("streams", NodeKind.QUEUE, "classic"),
            ("exchanges", NodeKind.QUEUE, "classic"),
        ],
    )
    def test_selected_policy_rejects_an_incompatible_resource(
        self,
        apply_to: str,
        kind: NodeKind,
        queue_type: str | None,
    ) -> None:
        with pytest.raises(TopologyParseError, match="incompatible apply-to"):
            parse_cluster_topology(
                _scoped_policy_response(
                    apply_to=apply_to,
                    kind=kind,
                    queue_type=queue_type,
                ),
                user_policy_selections=_policy_selections(
                    vhost="t",
                    name="resource",
                    kind=kind,
                    policy_name="scoped-route",
                ),
            )

    def test_policies_do_not_create_routes_for_shovels(self) -> None:
        response = _response(
            parameters=[_shovel_param("resource", "t")],
            policies=[
                _policy(
                    "all-resources",
                    ".*",
                    "all",
                    0,
                    definition={"dead-letter-exchange": "route-target"},
                )
            ],
        )

        topology = parse_cluster_topology(response)

        assert len(topology.shovels) == 1
        assert topology.edges == frozenset()


class TestPolicySelectionEvidence:
    def test_broker_style_regex_is_not_evaluated_locally(self) -> None:
        response = _response(
            vhosts=[_vhost("v")],
            queues=[_queue("q", "v", **{"x-queue-type": "classic"})],
            policies=[
                _policy(
                    "p",
                    "(?<broker_style_name>q)",
                    "queues",
                    0,
                    vhost="v",
                    definition={"dead-letter-exchange": "dlx"},
                )
            ],
        )

        topology = parse_cluster_topology(
            response,
            user_policy_selections=_policy_selections(vhost="v", name="q", kind=NodeKind.QUEUE, policy_name="p"),
        )

        (edge,) = topology.edges
        assert edge.target.name == "dlx"

    def test_routing_relevant_policy_without_evidence_fails_closed(self) -> None:
        response = _response(
            queues=[_queue("q", "t", **{"x-queue-type": "classic"})],
            policies=[_policy("p", "q", "queues", 0, definition={"dead-letter-exchange": "dlx"})],
        )

        with pytest.raises(TopologyParseError, match="QueueResponse and ExchangeResponse"):
            parse_cluster_topology(response)

    def test_observed_absence_of_a_policy_prevents_a_policy_derived_route(self) -> None:
        response = _response(
            vhosts=[_vhost("t")],
            queues=[_queue("q", "t", **{"x-queue-type": "classic"})],
            policies=[_policy("p", "q", "queues", 0, definition={"dead-letter-exchange": "dlx"})],
        )

        topology = parse_cluster_topology(
            response,
            user_policy_selections=_policy_selections(vhost="t", name="q", kind=NodeKind.QUEUE, policy_name=None),
        )

        assert topology.edges == frozenset()

    def test_incompatible_observed_policy_reports_its_runtime_string_value(self) -> None:
        response = _response(
            vhosts=[_vhost("t")],
            queues=[_queue("q", "t", **{"x-queue-type": "classic"})],
            policies=[
                _policy(
                    "exchange-only",
                    ".*",
                    "exchanges",
                    0,
                    definition={"dead-letter-exchange": "dlx"},
                )
            ],
        )

        with pytest.raises(TopologyParseError, match="incompatible apply-to value 'exchanges'"):
            parse_cluster_topology(
                response,
                user_policy_selections=_policy_selections(
                    vhost="t",
                    name="q",
                    kind=NodeKind.QUEUE,
                    policy_name="exchange-only",
                ),
            )


class TestDeadLetterEdges:
    def test_argument_only(self) -> None:
        response = _response(
            queues=[
                _queue(
                    "q1",
                    "v",
                    **{
                        "x-queue-type": "classic",
                        "x-dead-letter-exchange": "dlx",
                        "x-dead-letter-routing-key": "rk",
                    },
                )
            ]
        )
        (edge,) = parse_cluster_topology(response).edges
        assert edge.target.name == "dlx"
        assert edge.routing_key == "rk"

    def test_no_dlx_declared_and_no_matching_policy_gives_no_edge(self) -> None:
        response = _response(queues=[_queue("q1", "v", **{"x-queue-type": "classic"})])
        assert parse_cluster_topology(response).edges == frozenset()

    def test_observed_policy_fallback_when_argument_absent(self) -> None:
        response = _response(
            queues=[_queue("orders.q", "t", **{"x-queue-type": "classic"})],
            policies=[
                _policy(
                    "orders-dlx",
                    "orders",
                    "queues",
                    10,
                    definition={"dead-letter-exchange": "orders.dlx", "dead-letter-routing-key": "failed"},
                )
            ],
        )
        (edge,) = parse_cluster_topology(
            response,
            user_policy_selections=_policy_selections(
                vhost="t", name="orders.q", kind=NodeKind.QUEUE, policy_name="orders-dlx"
            ),
        ).edges
        assert edge.target.name == "orders.dlx"
        assert edge.routing_key == "failed"

    def test_per_key_merge_argument_dlx_wins_but_policy_routing_key_fills_gap(self) -> None:
        """The subtle case: RabbitMQ's precedence is per-KEY, not
        per-resource -- a queue that sets its own DLX but not its own
        routing key can still borrow the routing key from policy."""
        response = _response(
            queues=[
                _queue(
                    "partial.q",
                    "t",
                    **{
                        "x-queue-type": "classic",
                        "x-dead-letter-exchange": "partial.dlx",
                    },
                )
            ],
            policies=[
                _policy(
                    "partial-dlx",
                    "partial",
                    "queues",
                    10,
                    definition={"dead-letter-exchange": "policy.dlx", "dead-letter-routing-key": "policy-rk"},
                )
            ],
        )
        (edge,) = parse_cluster_topology(
            response,
            user_policy_selections=_policy_selections(
                vhost="t", name="partial.q", kind=NodeKind.QUEUE, policy_name="partial-dlx"
            ),
        ).edges
        assert edge.target.name == "partial.dlx"  # argument wins
        assert edge.routing_key == "policy-rk"  # policy fills the gap

    def test_catch_all_policy_without_dlx_key_does_not_leak_an_edge(self) -> None:
        response = _response(
            queues=[_queue("q1", "t", **{"x-queue-type": "classic"})],
            policies=[_policy("catch-all", ".*", "all", 0, definition={"max-length": 1000})],
        )
        assert parse_cluster_topology(response).edges == frozenset()

    def test_routing_relevant_policies_without_evidence_do_not_fabricate_a_dead_letter_route(self) -> None:
        response = _response(
            vhosts=[_vhost("t")],
            queues=[_queue("q1", "t", **{"x-queue-type": "classic"})],
            policies=[
                _policy("a", "q", "queues", 10, definition={"dead-letter-exchange": "a.dlx"}),
                _policy("b", "q", "queues", 10, definition={"dead-letter-exchange": "b.dlx"}),
            ],
        )

        with pytest.raises(TopologyParseError, match="QueueResponse and ExchangeResponse"):
            parse_cluster_topology(response)

    def test_broker_selected_policy_resolves_a_conflicting_dead_letter_route(self) -> None:
        response = _response(
            vhosts=[_vhost("t")],
            queues=[_queue("q1", "t", **{"x-queue-type": "classic"})],
            policies=[
                _policy("a", "q", "queues", 10, definition={"dead-letter-exchange": "a.dlx"}),
                _policy("b", "q", "queues", 10, definition={"dead-letter-exchange": "b.dlx"}),
            ],
        )
        selections = {NodeId(vhost="t", name="q1", kind=NodeKind.QUEUE): "b"}

        (edge,) = parse_cluster_topology(response, user_policy_selections=selections).edges

        assert edge.target.name == "b.dlx"

    def test_unknown_broker_selected_policy_is_rejected(self) -> None:
        response = _response(
            queues=[_queue("q1", "t", **{"x-queue-type": "classic"})],
            policies=[
                _policy("a", "q", "queues", 10, definition={"dead-letter-exchange": "a.dlx"}),
                _policy("b", "q", "queues", 10, definition={"dead-letter-exchange": "b.dlx"}),
            ],
        )
        selections = {NodeId(vhost="t", name="q1", kind=NodeKind.QUEUE): "not-a-candidate"}

        with pytest.raises(TopologyParseError, match="does not identify exactly one policy"):
            parse_cluster_topology(response, user_policy_selections=selections)

    def test_observed_policy_is_used_even_when_policies_have_the_same_dead_letter_route(self) -> None:
        response = _response(
            queues=[_queue("q1", "t", **{"x-queue-type": "classic"})],
            policies=[
                _policy("a", "q", "queues", 10, definition={"dead-letter-exchange": "shared.dlx"}),
                _policy("b", "q", "queues", 10, definition={"dead-letter-exchange": "shared.dlx"}),
            ],
        )

        (edge,) = parse_cluster_topology(
            response,
            user_policy_selections=_policy_selections(vhost="t", name="q1", kind=NodeKind.QUEUE, policy_name="a"),
        ).edges
        assert edge.target.name == "shared.dlx"


class TestAlternateExchangeEdges:
    def test_argument_only(self) -> None:
        response = _response(exchanges=[_exchange("ex1", "v", **{"alternate-exchange": "ae"})])
        (edge,) = parse_cluster_topology(response).edges
        assert edge.target.name == "ae"
        assert edge.routing_key is None  # not applicable to this edge kind

    def test_observed_policy_fallback_when_argument_absent(self) -> None:
        response = _response(
            exchanges=[_exchange("ex1", "t")],
            policies=[_policy("ex-ae", "ex", "exchanges", 10, definition={"alternate-exchange": "ae.exchange"})],
        )
        (edge,) = parse_cluster_topology(
            response,
            user_policy_selections=_policy_selections(
                vhost="t", name="ex1", kind=NodeKind.EXCHANGE, policy_name="ex-ae"
            ),
        ).edges
        assert edge.target.name == "ae.exchange"

    def test_routing_relevant_policies_without_evidence_do_not_fabricate_an_alternate_exchange(self) -> None:
        response = _response(
            exchanges=[_exchange("ex1", "t")],
            policies=[
                _policy("a", "ex", "exchanges", 10, definition={"alternate-exchange": "a.ae"}),
                _policy("b", "ex", "exchanges", 10, definition={"alternate-exchange": "b.ae"}),
            ],
        )

        with pytest.raises(TopologyParseError, match="QueueResponse and ExchangeResponse"):
            parse_cluster_topology(response)

    def test_broker_selected_policy_resolves_a_conflicting_alternate_exchange(self) -> None:
        response = _response(
            exchanges=[_exchange("ex1", "t")],
            policies=[
                _policy("a", "ex", "exchanges", 10, definition={"alternate-exchange": "a.ae"}),
                _policy("b", "ex", "exchanges", 10, definition={"alternate-exchange": "b.ae"}),
            ],
        )
        selections = {NodeId(vhost="t", name="ex1", kind=NodeKind.EXCHANGE): "a"}

        (edge,) = parse_cluster_topology(response, user_policy_selections=selections).edges

        assert edge.target.name == "a.ae"
