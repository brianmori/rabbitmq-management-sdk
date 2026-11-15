"""Check contextual routing coverage independently of degree-only findings."""

import pickle
from dataclasses import replace

import pytest
from tests.shared.parser_fixtures import _binding, _exchange, _queue, _response, _vhost

from rabbitmq_management_sdk import ClusterAuditor, TopologyAnalysisError
from rabbitmq_management_sdk.topology import (
    ClusterTopology,
    DeadLetterRoutingIssue,
    EdgeKind,
    ExchangeRoutingCoverage,
    RoutingStatus,
    RoutingUncertainty,
    TopologyEdge,
    node_sort_key,
)

pytestmark = pytest.mark.unit


def _auditor(exchange_type: str = "direct", binding_key: str = "accepted") -> ClusterAuditor:
    return ClusterAuditor(
        _response(
            vhosts=[_vhost("v")],
            exchanges=[_exchange("entry", "v", exchange_type)],
            queues=[_queue("sink", "v", **{"x-queue-type": "classic"})],
            bindings=[_binding("entry", "sink", binding_key)],
        )
    )


def _entry(auditor: ClusterAuditor, routing_key: str | None = None) -> ExchangeRoutingCoverage:
    return next(
        result
        for result in auditor.exchange_routing_coverage(routing_key=routing_key)
        if result.exchange.id.name == "entry"
    )


def test_key_mismatch_is_distinct_from_degree_based_black_holes() -> None:
    auditor = _auditor()

    assert auditor.black_hole_exchanges() == ()
    assert _entry(auditor).status == RoutingStatus.UNDETERMINED
    assert _entry(auditor, "accepted").status == RoutingStatus.DESTINATION_POSSIBLE
    assert _entry(auditor, "rejected").status == RoutingStatus.NO_DESTINATION
    assert _entry(auditor, "rejected").routing_key == "rejected"
    assert _entry(auditor, "accepted").destinations[0].name == "sink"
    assert auditor.black_hole_exchanges() == ()


def test_every_contained_exchange_is_returned_including_default_and_internal() -> None:
    source = _auditor("fanout")
    data = source.definitions.model_dump(by_alias=True)
    data["exchanges"].extend(
        [_exchange("", "v"), _exchange("discard", "v"), {**_exchange("internal", "v", "fanout"), "internal": True}]
    )
    data["bindings"].append(_binding("internal", "sink"))
    auditor = ClusterAuditor(type(source.definitions).model_validate(data))

    results = auditor.exchange_routing_coverage()

    assert [result.exchange.id.name for result in results] == ["", "discard", "entry", "internal"]
    assert [result.status for result in results] == [
        RoutingStatus.UNDETERMINED,
        RoutingStatus.NO_DESTINATION,
        RoutingStatus.DESTINATION_POSSIBLE,
        RoutingStatus.DESTINATION_POSSIBLE,
    ]
    assert [exchange.id.name for exchange in auditor.black_hole_exchanges()] == ["discard"]
    assert results[-1].exchange.internal


def test_unknown_key_and_explicit_empty_key_are_distinct() -> None:
    auditor = _auditor(binding_key="")
    assert _entry(auditor, "").status == RoutingStatus.DESTINATION_POSSIBLE
    assert _entry(auditor).status == RoutingStatus.UNDETERMINED
    assert _entry(auditor, "").routing_key == ""
    assert _entry(auditor).routing_key is None


@pytest.mark.parametrize(
    "key, expected",
    [("orders.failed", RoutingStatus.DESTINATION_POSSIBLE), ("orders.failed.extra", RoutingStatus.NO_DESTINATION)],
)
def test_exchange_chains_preserve_key_compatibility(key: str, expected: RoutingStatus) -> None:
    source = _auditor()
    data = source.definitions.model_dump(by_alias=True)
    data["exchanges"] = [_exchange("entry", "v", "fanout"), _exchange("topic", "v", "topic")]
    data["bindings"] = [_binding("entry", "topic", destination_type="exchange"), _binding("topic", "sink", "orders.*")]
    auditor = ClusterAuditor(type(source.definitions).model_validate(data))

    assert _entry(auditor, key).status == expected
    assert auditor.black_hole_exchanges() == ()


def test_exchange_only_cycle_with_outgoing_bindings_has_no_queue_destination() -> None:
    auditor = ClusterAuditor(
        _response(
            exchanges=[_exchange(name, "v", "fanout") for name in ("entry", "other")],
            bindings=[
                _binding("entry", "other", destination_type="exchange"),
                _binding("other", "entry", destination_type="exchange"),
            ],
        )
    )
    assert auditor.black_hole_exchanges() == ()
    assert all(result.status == RoutingStatus.NO_DESTINATION for result in auditor.exchange_routing_coverage())
    assert len(auditor.structural_cycles().cycles) == 1


def test_matching_exchange_binding_suppresses_parent_fallback() -> None:
    auditor = ClusterAuditor(
        _response(
            exchanges=[
                _exchange("entry", "v", **{"alternate-exchange": "fallback"}),
                _exchange("dead-end", "v"),
                _exchange("fallback", "v", "fanout"),
            ],
            queues=[_queue("sink", "v", **{"x-queue-type": "classic"})],
            bindings=[
                _binding("entry", "dead-end", "accepted", destination_type="exchange"),
                _binding("fallback", "sink"),
            ],
        )
    )
    assert _entry(auditor, "accepted").status == RoutingStatus.NO_DESTINATION
    assert _entry(auditor, "rejected").status == RoutingStatus.DESTINATION_POSSIBLE
    assert _entry(auditor).status == RoutingStatus.UNDETERMINED


def test_nonempty_headers_remain_unknown_with_a_supplied_routing_key() -> None:
    source = _auditor("headers")
    data = source.definitions.model_dump(by_alias=True)
    data["bindings"][0]["arguments"] = {"department": "sales"}
    result = _entry(ClusterAuditor(type(source.definitions).model_validate(data)), "accepted")
    assert result.status == RoutingStatus.UNDETERMINED
    assert result.issues[0].reason == RoutingUncertainty.MESSAGE_HEADERS_NOT_CAPTURED


def test_unsupported_plugin_semantics_remain_explicit() -> None:
    auditor = ClusterAuditor(_response(exchanges=[_exchange("plugin", "v", "x-plugin")]))
    (result,) = auditor.exchange_routing_coverage()
    assert result.status == RoutingStatus.UNDETERMINED
    assert result.issues[0].reason == RoutingUncertainty.UNSUPPORTED_ROUTING_SEMANTICS
    assert auditor.black_hole_exchanges() == (result.exchange,)


def test_matching_dangling_target_is_undetermined() -> None:
    source = _auditor("fanout")
    topology = replace(source.topology, queues=frozenset())
    result = _entry(ClusterAuditor.from_topology(topology), "accepted")
    assert result.status == RoutingStatus.UNDETERMINED
    assert result.destinations == ()
    assert result.issues[0].reason == RoutingUncertainty.MISSING_RESOURCE


def test_default_exchange_uses_queue_names_without_explicit_bindings() -> None:
    source = _auditor()
    data = source.definitions.model_dump(by_alias=True)
    data["exchanges"] = [_exchange("", "v")]
    data["bindings"] = []
    auditor = ClusterAuditor(type(source.definitions).model_validate(data))
    (matched,) = auditor.exchange_routing_coverage(routing_key="sink")
    (unmatched,) = auditor.exchange_routing_coverage(routing_key="other")
    (unknown,) = auditor.exchange_routing_coverage()
    assert matched.status == RoutingStatus.DESTINATION_POSSIBLE
    assert matched.destinations[0].name == "sink"
    assert unmatched.status == RoutingStatus.NO_DESTINATION
    assert unknown.status == RoutingStatus.UNDETERMINED
    assert auditor.black_hole_exchanges() == ()


def test_traversal_stops_at_queues_before_later_dead_letter_operations() -> None:
    source = _auditor("fanout")
    queue = next(iter(source.topology.queues))
    exchange = next(iter(source.topology.exchanges))
    edge = TopologyEdge(queue.id, exchange.id, EdgeKind.DEAD_LETTER)
    topology = replace(source.topology, edges=source.topology.edges | {edge})
    result = _entry(ClusterAuditor.from_topology(topology))
    assert result.status == RoutingStatus.DESTINATION_POSSIBLE
    assert result.destinations == (queue.id,)


def test_same_named_exchanges_and_queues_stay_separate_by_vhost() -> None:
    auditor = ClusterAuditor(
        _response(
            exchanges=[_exchange("entry", vhost) for vhost in ("b", "a")],
            queues=[_queue("sink", vhost, **{"x-queue-type": "classic"}) for vhost in ("b", "a")],
            bindings=[_binding("entry", "sink", "accepted", vhost=vhost) for vhost in ("b", "a")],
        )
    )
    results = auditor.exchange_routing_coverage(routing_key="accepted")
    assert [result.exchange.id.vhost for result in results] == ["a", "b"]
    assert all(result.destinations[0].vhost == result.exchange.id.vhost for result in results)
    assert [result.exchange.id for result in results] == sorted(
        auditor.topology.all_node_ids - {queue.id for queue in auditor.topology.queues}, key=node_sort_key
    )


def test_calls_with_different_keys_do_not_change_the_aggregate_report_context() -> None:
    auditor = _auditor()
    original = auditor.exchange_routing_coverage()
    matched = auditor.exchange_routing_coverage(routing_key="accepted")
    auditor.exchange_routing_coverage(routing_key="rejected")
    assert matched[0].status == RoutingStatus.DESTINATION_POSSIBLE
    assert auditor.audit().exchange_routing_coverage == original
    assert auditor.exchange_routing_coverage() == original
    assert all(result.routing_key is None for result in auditor.audit().exchange_routing_coverage)


@pytest.mark.parametrize("exchange_type", ["direct", "fanout", "headers"])
def test_shared_engine_preserves_dlx_public_result_types(exchange_type: str) -> None:
    source = _auditor(exchange_type)
    data = source.definitions.model_dump(by_alias=True)
    data["queues"].append(
        _queue(
            "work",
            "v",
            **{"x-queue-type": "classic", "x-dead-letter-exchange": "entry", "x-dead-letter-routing-key": "accepted"},
        )
    )
    if exchange_type == "headers":
        data["bindings"][0]["arguments"] = {"department": "sales"}
    auditor = ClusterAuditor(type(source.definitions).model_validate(data))
    dlx = next(result for result in auditor.dead_letter_coverage() if result.queue.id.name == "work")
    exchange = _entry(auditor, dlx.routing_key)
    assert dlx.status.value == exchange.status.value
    assert dlx.destinations == exchange.destinations
    assert [(issue.resource, issue.reason.value) for issue in dlx.issues] == [
        (issue.resource, issue.reason.value) for issue in exchange.issues
    ]
    assert all(isinstance(issue, DeadLetterRoutingIssue) for issue in dlx.issues)
    assert pickle.loads(pickle.dumps(dlx)) == dlx


@pytest.mark.parametrize("routing_key", [0, False, [], {}])
def test_invalid_key_is_rejected_even_for_an_empty_graph(routing_key: object) -> None:
    with pytest.raises(TopologyAnalysisError, match="routing_key must be str or None"):
        ClusterAuditor(_response()).exchange_routing_coverage(routing_key=routing_key)  # type: ignore[arg-type]


def test_empty_graph_has_empty_exchange_coverage() -> None:
    topology = ClusterTopology(frozenset(), frozenset(), frozenset(), frozenset())
    assert ClusterAuditor.from_topology(topology).exchange_routing_coverage() == ()
