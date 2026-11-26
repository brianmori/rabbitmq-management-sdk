"""Evaluate captured DLX routing independently of queue naming conventions."""

from dataclasses import replace

import pytest
from tests.shared.parser_fixtures import _binding, _exchange, _observed_queue, _policy, _queue, _response, _vhost

from rabbitmq_management_sdk import ClusterAuditor
from rabbitmq_management_sdk.resources.v4.admin.schemas.export_response import ClusterDefinitionsResponse
from rabbitmq_management_sdk.resources.v4.exchanges.schemas.exchange_response import ExchangeResponse
from rabbitmq_management_sdk.topology import (
    ClusterTopology,
    DeadLetterCoverage,
    EdgeKind,
    NodeId,
    NodeKind,
    QueueNode,
    TopologyEdge,
)
from rabbitmq_management_sdk.topology import (
    DeadLetterCoverageStatus as Status,
)
from rabbitmq_management_sdk.topology import (
    DeadLetterUncertainty as Uncertainty,
)

pytestmark = pytest.mark.unit


def _auditor(
    exchange_type: str = "direct",
    routing_key: str | None = "failed",
    binding_key: str = "failed",
    binding_arguments: dict[str, object] | None = None,
) -> ClusterAuditor:
    arguments: dict[str, object] = {"x-queue-type": "classic", "x-dead-letter-exchange": "dlx"}
    if routing_key is not None:
        arguments["x-dead-letter-routing-key"] = routing_key
    return ClusterAuditor(
        _response(
            vhosts=[_vhost("v")],
            queues=[_queue("work", "v", **arguments), _queue("sink", "v", **{"x-queue-type": "classic"})],
            exchanges=[_exchange("dlx", "v", exchange_type)],
            bindings=[_binding("dlx", "sink", binding_key, arguments=binding_arguments)],
        )
    )


def _work(auditor: ClusterAuditor) -> DeadLetterCoverage:
    return next(result for result in auditor.dead_letter_coverage() if result.queue.id.name == "work")


@pytest.mark.parametrize("declared_exchange", [True, False], ids=["declared", "observed-only"])
def test_omitted_exchange_arguments_leave_dlx_destinations_undetermined(declared_exchange: bool) -> None:
    arguments = {"x-queue-type": "classic", "x-dead-letter-exchange": "dlx", "x-dead-letter-routing-key": "retry"}
    exchange = _exchange("dlx", "v")
    del exchange["arguments"]
    auditor = ClusterAuditor(
        _response(
            vhosts=[_vhost("v")],
            queues=[_queue("work", "v", **arguments)],
            exchanges=[exchange] if declared_exchange else [],
        ),
        queues=[_observed_queue("work", "v", type="classic", arguments=arguments, policy=None)],
        exchanges=[ExchangeResponse.model_validate({**exchange, "policy": None})],
    )
    coverage = _work(auditor)
    dlx = NodeId("v", "dlx", NodeKind.EXCHANGE)
    assert coverage.status == Status.UNDETERMINED
    assert coverage.dead_letter_exchange == dlx
    assert not coverage.destinations
    assert [(issue.resource, issue.reason) for issue in coverage.issues] == [
        (dlx, Uncertainty.EXCHANGE_ARGUMENTS_NOT_CAPTURED)
    ]
    assert auditor.audit().dead_letter_coverage == (coverage,)


def test_all_queues_remain_visible_including_terminal_and_discard_names() -> None:
    names = ["terminal.plq", "discard", "work"]
    auditor = ClusterAuditor(_response(queues=[_queue(name, "v", **{"x-queue-type": "classic"}) for name in names]))

    assert [(result.queue.id.name, result.status) for result in auditor.dead_letter_coverage()] == [
        (name, Status.NO_DLX) for name in sorted(names)
    ]
    assert auditor.audit().dead_letter_coverage == auditor.dead_letter_coverage()


@pytest.mark.parametrize("policy_key", ["dead-letter-exchange", "dead-letter-routing-key"])
def test_classic_dlx_name_is_retained_when_policy_selection_is_omitted(policy_key: str) -> None:
    classic_arguments = {"x-queue-type": "classic"}
    arguments = classic_arguments.copy()
    if policy_key == "dead-letter-routing-key":
        arguments["x-dead-letter-exchange"] = "dlx"
    effective_definition: dict[str, object] = {policy_key: "dlx" if policy_key == "dead-letter-exchange" else "retry"}
    exchange = _exchange("dlx", "v", "direct")
    auditor = ClusterAuditor(
        _response(
            vhosts=[_vhost("v")],
            queues=[_queue("work", "v", **arguments), _queue("sink", "v", **classic_arguments)],
            exchanges=[exchange],
            bindings=[_binding("dlx", "sink")],
            policies=[_policy("retry", "^work$", "classic_queues", 5, vhost="v", definition=effective_definition)],
        ),
        queues=[
            _observed_queue(
                "work",
                "v",
                type="classic",
                arguments=arguments,
                effective_policy_definition=effective_definition,
            ),
            _observed_queue("sink", "v", type="classic", arguments=classic_arguments, policy=None),
        ],
        exchanges=[ExchangeResponse.model_validate(exchange)],
    )

    sink, work = auditor.dead_letter_coverage()

    assert sink.status == Status.NO_DLX
    assert work.status == Status.UNDETERMINED
    assert work.dead_letter_exchange == NodeId(vhost="v", name="dlx", kind=NodeKind.EXCHANGE)
    assert work.routing_key is None
    assert work.destinations == ()
    dlx_edges = [edge for edge in auditor.topology.edges if edge.kind == EdgeKind.DEAD_LETTER]
    if policy_key == "dead-letter-exchange":
        assert [(issue.resource, issue.reason) for issue in work.issues] == [
            (work.queue.id, Uncertainty.DLX_ROUTE_NOT_CAPTURED)
        ]
        assert not dlx_edges
    else:
        (edge,) = dlx_edges
        assert edge.target == work.dead_letter_exchange
        assert edge.routing_key is None
        assert [(issue.resource, issue.reason) for issue in work.issues] == [
            (edge.target, Uncertainty.ROUTING_KEY_NOT_CAPTURED)
        ]
    assert _work(ClusterAuditor.from_topology(auditor.topology)) == work
    assert auditor.audit().dead_letter_coverage == (sink, work)


def test_omitted_policy_evidence_cannot_rule_out_a_classic_dlx() -> None:
    arguments = {"x-queue-type": "classic"}
    queue = _queue("work", "v", **arguments)
    definitions = _response(
        queues=[queue],
        policies=[
            _policy("retry", "^work$", "classic_queues", 5, vhost="v", definition={"dead-letter-exchange": "dlx"})
        ],
    )
    observation = _observed_queue("work", "v", type="classic", arguments=arguments)
    missing_effective_policy = ClusterAuditor(definitions, queues=[observation], exchanges=[])
    missing_policy_section = ClusterAuditor(ClusterDefinitionsResponse.model_validate({"queues": [queue]}))
    for auditor in (missing_effective_policy, missing_policy_section):
        coverage = _work(auditor)
        assert coverage.status == Status.UNDETERMINED
        assert coverage.dead_letter_exchange is None
        assert [issue.reason for issue in coverage.issues] == [Uncertainty.DLX_ROUTE_NOT_CAPTURED]
        assert not auditor.topology.edges

    observation_with_empty_policy = _observed_queue(
        "work", "v", type="classic", arguments=arguments, effective_policy_definition={}
    )
    assert _work(ClusterAuditor(definitions, queues=[observation_with_empty_policy], exchanges=[])).status == (
        Status.NO_DLX
    )


def test_missing_exchange_and_empty_exchange_are_distinct() -> None:
    auditor = _auditor()
    missing = replace(
        auditor.topology,
        exchanges=frozenset(),
        edges=frozenset(edge for edge in auditor.topology.edges if edge.kind == EdgeKind.DEAD_LETTER),
    )
    empty = replace(auditor.topology, edges=missing.edges)

    assert _work(ClusterAuditor.from_topology(missing)).status == Status.MISSING_EXCHANGE
    assert _work(ClusterAuditor.from_topology(empty)).status == Status.NO_DESTINATION


@pytest.mark.parametrize(
    "exchange_type, key, binding, matches",
    [
        ("direct", "failed", "failed", True),
        ("direct", "failed", "success", False),
        ("direct", "", "", True),
        ("direct", "", "failed", False),
        ("topic", "orders.failed", "orders.*", True),
        ("topic", "orders.failed.extra", "orders.*", False),
        ("topic", "orders", "orders.#", True),
        ("topic", "orders.failed.extra", "orders.#", True),
        ("topic", "a.x.y.b", "a.#.b", True),
        ("topic", "a.b", "a.#.b", True),
        ("topic", "a.x.b.y", "a.#.b", False),
        ("topic", "", "#", True),
        ("topic", "", "*", False),
        ("topic", "", "", True),
        ("topic", ".", "*.*", True),
        ("topic", "#", "#", True),
        ("topic", "a-b", "a.*", False),
        ("fanout", "anything", "ignored", True),
    ],
)
def test_known_key_routing(exchange_type: str, key: str, binding: str, matches: bool) -> None:
    result = _work(_auditor(exchange_type, key, binding))

    assert result.status == (Status.DESTINATION_POSSIBLE if matches else Status.NO_DESTINATION)
    assert [queue.name for queue in result.destinations] == (["sink"] if matches else [])
    assert result.issues == ()


@pytest.mark.parametrize(
    "exchange_type, binding_key, expected",
    [
        ("direct", "failed", Status.UNDETERMINED),
        ("topic", "orders.*", Status.UNDETERMINED),
        ("topic", "#", Status.DESTINATION_POSSIBLE),
        ("topic", "#.#", Status.DESTINATION_POSSIBLE),
        ("fanout", "ignored", Status.DESTINATION_POSSIBLE),
    ],
)
def test_original_routing_keys_are_not_inferred(exchange_type: str, binding_key: str, expected: Status) -> None:
    result = _work(_auditor(exchange_type, None, binding_key))

    assert result.status == expected
    assert result.routing_key is None
    assert [issue.reason for issue in result.issues] == (
        [Uncertainty.ROUTING_KEY_NOT_CAPTURED] if expected == Status.UNDETERMINED else []
    )


@pytest.mark.parametrize("mode", ["all", "any", "all-with-x", "any-with-x"])
def test_required_message_headers_are_unknown(mode: str) -> None:
    result = _work(_auditor("headers", binding_arguments={"x-match": mode, "department": "sales"}))

    assert result.status == Status.UNDETERMINED
    assert result.destinations == ()
    assert result.issues[0].reason == Uncertainty.MESSAGE_HEADERS_NOT_CAPTURED


@pytest.mark.parametrize(
    "arguments, expected",
    [
        ({}, True),
        ({"x-match": "all"}, True),
        ({"x-match": "any"}, False),
        ({"x-match": "all-with-x"}, True),
        ({"x-match": "any-with-x"}, False),
        ({"x-match": "all", "x-ignored": 1}, True),
        ({"x-match": "any", "x-ignored": 1}, False),
    ],
)
def test_header_bindings_without_required_headers_are_decidable(arguments: dict[str, object], expected: bool) -> None:
    result = _work(_auditor("headers", binding_arguments=arguments))

    assert result.status == (Status.DESTINATION_POSSIBLE if expected else Status.NO_DESTINATION)


def test_with_x_modes_require_x_headers() -> None:
    result = _work(_auditor("headers", binding_arguments={"x-match": "all-with-x", "x-custom": "value"}))
    assert result.status == Status.UNDETERMINED


@pytest.mark.parametrize("exchange_type", ["direct", "fanout", "topic"])
def test_alternate_fallback_is_suppressed_by_matching_exchange_binding(exchange_type: str) -> None:
    source = _auditor(exchange_type)
    data = source.definitions.model_dump(by_alias=True)
    data["exchanges"] = [
        _exchange("dlx", "v", exchange_type, **{"alternate-exchange": "fallback"}),
        _exchange("dead-end", "v"),
        _exchange("fallback", "v", "fanout"),
    ]
    data["bindings"] = [
        _binding("dlx", "dead-end", "failed", destination_type="exchange"),
        _binding("fallback", "sink"),
    ]
    auditor = ClusterAuditor(type(source.definitions).model_validate(data))

    result = _work(auditor)

    assert result.status == Status.NO_DESTINATION
    assert result.destinations == ()


def test_alternates_are_followed_only_at_exchanges_with_no_matching_binding() -> None:
    auditor = ClusterAuditor(
        _response(
            vhosts=[_vhost("v")],
            queues=[
                _queue(
                    "work",
                    "v",
                    **{
                        "x-queue-type": "classic",
                        "x-dead-letter-exchange": "dlx",
                        "x-dead-letter-routing-key": "failed",
                    },
                ),
                _queue("sink", "v", **{"x-queue-type": "classic"}),
            ],
            exchanges=[
                _exchange("dlx", "v", **{"alternate-exchange": "fallback"}),
                _exchange("fallback", "v", "topic", **{"alternate-exchange": "parking"}),
                _exchange("parking", "v", "fanout"),
            ],
            bindings=[_binding("dlx", "sink", "success"), _binding("parking", "sink")],
        )
    )

    assert _work(auditor).status == Status.DESTINATION_POSSIBLE
    assert [queue.name for queue in _work(auditor).destinations] == ["sink"]


def test_exchange_and_alternate_cycles_terminate_without_a_destination() -> None:
    source = _auditor("fanout")
    exchange = next(iter(source.topology.exchanges))
    edges = frozenset(edge for edge in source.topology.edges if edge.kind == EdgeKind.DEAD_LETTER)
    for kind in (EdgeKind.BINDING, EdgeKind.ALTERNATE_EXCHANGE):
        topology = replace(source.topology, edges=edges | {TopologyEdge(exchange.id, exchange.id, kind)})
        assert _work(ClusterAuditor.from_topology(topology)).status == Status.NO_DESTINATION


@pytest.mark.parametrize(
    "pattern, expected", [("orders.*", Status.DESTINATION_POSSIBLE), ("success.#", Status.NO_DESTINATION)]
)
def test_exchange_chains_preserve_the_dead_letter_routing_key(pattern: str, expected: Status) -> None:
    source = _auditor(routing_key="orders.failed")
    data = source.definitions.model_dump(by_alias=True)
    data["exchanges"].extend([_exchange("topic", "v", "topic"), _exchange("fanout", "v", "fanout")])
    data["bindings"] = [
        _binding("dlx", "topic", "orders.failed", destination_type="exchange"),
        _binding("topic", "fanout", pattern, destination_type="exchange"),
        _binding("fanout", "sink"),
    ]
    result = _work(ClusterAuditor(type(source.definitions).model_validate(data)))
    assert result.status == expected
    assert result.routing_key == "orders.failed"
    assert [node.name for node in result.destinations] == (["sink"] if expected == Status.DESTINATION_POSSIBLE else [])


@pytest.mark.parametrize("kind", [NodeKind.QUEUE, NodeKind.EXCHANGE])
def test_dangling_matching_destinations_are_undetermined(kind: NodeKind) -> None:
    source = _auditor("fanout")
    binding = next(edge for edge in source.topology.edges if edge.kind == EdgeKind.BINDING)
    target = replace(binding.target, name="missing", kind=kind)
    topology = replace(
        source.topology,
        edges=frozenset(replace(edge, target=target) if edge == binding else edge for edge in source.topology.edges),
    )

    result = _work(ClusterAuditor.from_topology(topology))

    assert result.status == Status.UNDETERMINED
    assert [(issue.resource, issue.reason) for issue in result.issues] == [(target, Uncertainty.MISSING_RESOURCE)]


@pytest.mark.parametrize(
    "key, expected",
    [
        ("sink", Status.DESTINATION_POSSIBLE),
        ("missing", Status.NO_DESTINATION),
        (None, Status.UNDETERMINED),
        ("", Status.NO_DESTINATION),
    ],
)
def test_default_dlx_uses_implicit_queue_names(key: str | None, expected: Status) -> None:
    source = _auditor(routing_key=key)
    data = source.definitions.model_dump(by_alias=True)
    data["queues"][0]["arguments"]["x-dead-letter-exchange"] = ""
    data["exchanges"] = []
    data["bindings"] = []
    auditor = ClusterAuditor(type(source.definitions).model_validate(data))

    result = _work(auditor)

    assert result.status == expected
    assert result.dead_letter_exchange is not None and result.dead_letter_exchange.name == ""
    assert [queue.name for queue in result.destinations] == (["sink"] if key == "sink" else [])


def test_unsupported_exchange_semantics_are_not_assumed_to_be_fanout() -> None:
    result = _work(_auditor("x-plugin-exchange"))
    assert result.status == Status.UNDETERMINED
    assert result.issues[0].reason == Uncertainty.UNSUPPORTED_ROUTING_SEMANTICS


def test_streams_are_not_applicable_and_custom_queue_types_are_undetermined() -> None:
    queue_id = NodeId(vhost="v", name="q", kind=NodeKind.QUEUE)
    for queue_type, status in (("stream", Status.NOT_APPLICABLE), ("plugin-queue", Status.UNDETERMINED)):
        queue = QueueNode(queue_id, queue_type, True)
        topology = ClusterTopology(frozenset(), frozenset({queue}), frozenset(), frozenset())
        (result,) = ClusterAuditor.from_topology(topology).dead_letter_coverage()
        assert result.status == status


def test_conflicting_effective_dlx_routes_are_undetermined() -> None:
    source = _auditor()
    edge = next(edge for edge in source.topology.edges if edge.kind == EdgeKind.DEAD_LETTER)
    topology = replace(
        source.topology, edges=source.topology.edges | {replace(edge, target=replace(edge.target, name="other"))}
    )
    result = _work(ClusterAuditor.from_topology(topology))
    assert result.status == Status.UNDETERMINED
    assert result.dead_letter_exchange is None
    assert result.issues[0].reason == Uncertainty.CONFLICTING_ROUTES


def test_known_destination_and_unknown_branch_remain_separate() -> None:
    source = _auditor("fanout")
    data = source.definitions.model_dump(by_alias=True)
    data["exchanges"].append(_exchange("headers", "v", "headers"))
    data["bindings"].extend(
        [
            _binding("dlx", "headers", destination_type="exchange"),
            _binding("headers", "sink", arguments={"department": "sales"}),
        ]
    )

    result = _work(ClusterAuditor(type(source.definitions).model_validate(data)))

    assert result.status == Status.DESTINATION_POSSIBLE
    assert [node.name for node in result.destinations] == ["sink"]
    assert result.issues[0].reason == Uncertainty.MESSAGE_HEADERS_NOT_CAPTURED


@pytest.mark.parametrize("arguments", ["{", "[]", '{"x-match": "unsupported"}'])
def test_incomplete_binding_data_in_manual_graph_is_undetermined(arguments: str) -> None:
    source = _auditor("headers")
    topology = replace(
        source.topology,
        edges=frozenset(
            replace(edge, arguments=arguments) if edge.kind == EdgeKind.BINDING else edge
            for edge in source.topology.edges
        ),
    )

    result = _work(ClusterAuditor.from_topology(topology))

    assert result.status == Status.UNDETERMINED
    assert result.issues[0].reason == Uncertainty.INCOMPLETE_BINDING


def test_unknown_selection_does_not_make_dead_end_a_possible_destination() -> None:
    source = _auditor("direct", None)
    data = source.definitions.model_dump(by_alias=True)
    data["exchanges"].append(_exchange("dead-end", "v"))
    data["bindings"] = [_binding("dlx", "dead-end", "failed", destination_type="exchange")]
    result = _work(ClusterAuditor(type(source.definitions).model_validate(data)))
    assert result.status == Status.NO_DESTINATION
    assert result.issues[0].reason == Uncertainty.ROUTING_KEY_NOT_CAPTURED


def test_unknown_binding_does_not_establish_an_unconditional_alternate_route() -> None:
    source = _auditor("direct", None)
    data = source.definitions.model_dump(by_alias=True)
    data["exchanges"] = [
        _exchange("dlx", "v", **{"alternate-exchange": "fallback"}),
        _exchange("fallback", "v", "fanout"),
    ]
    data["bindings"].append(_binding("fallback", "sink"))
    result = _work(ClusterAuditor(type(source.definitions).model_validate(data)))
    assert result.status == Status.UNDETERMINED
    assert result.destinations == ()


def test_conflicting_alternate_routes_are_undetermined() -> None:
    source = _auditor()
    exchange = next(iter(source.topology.exchanges))
    edges = frozenset(edge for edge in source.topology.edges if edge.kind == EdgeKind.DEAD_LETTER)
    first = TopologyEdge(exchange.id, replace(exchange.id, name="first"), EdgeKind.ALTERNATE_EXCHANGE)
    second = TopologyEdge(exchange.id, replace(exchange.id, name="second"), EdgeKind.ALTERNATE_EXCHANGE)
    result = _work(ClusterAuditor.from_topology(replace(source.topology, edges=edges | {first, second})))
    assert result.status == Status.UNDETERMINED
    assert Uncertainty.CONFLICTING_ROUTES in {issue.reason for issue in result.issues}
