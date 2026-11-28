"""Exercise the public comparison with captured declarations and observations."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import TYPE_CHECKING

import pytest
from tests.shared.parser_fixtures import _binding, _exchange, _observed_queue, _policy, _queue, _response, _vhost

from rabbitmq_management_sdk import ClusterAuditor, TopologyAnalysisError, compare_captures
from rabbitmq_management_sdk.resources.v4.admin.schemas.export_response import ClusterDefinitionsResponse
from rabbitmq_management_sdk.topology import (
    BindingComparison,
    ComparisonGap,
    NodeId,
    NodeKind,
    PolicyKind,
    PolicySelectionStatus,
    RoutingCondition,
    RoutingContract,
    RoutingIssue,
    RoutingUncertainty,
    RoutingVerdict,
    edge_sort_key,
)

if TYPE_CHECKING:
    from rabbitmq_management_sdk.resources.v4.queues.schemas.queue_response import QueueResponse

pytestmark = pytest.mark.unit

_CLUSTER = "rabbitmq-cluster-id-example"
_VHOST = "orders"


def _capture(cluster_id: str | None = _CLUSTER, **sections: object) -> ClusterDefinitionsResponse:
    defaults: dict[str, object] = {
        "global_parameters": [{"name": "cluster_name", "value": "orders-broker"}],
        "vhosts": [_vhost(_VHOST)],
        "queues": [_queue("worker", _VHOST), _queue("archive", _VHOST)],
        "exchanges": [_exchange("events", _VHOST)],
        "bindings": [_binding("events", "worker", vhost=_VHOST), _binding("events", "archive", "log", vhost=_VHOST)],
    }
    if cluster_id is not None:
        defaults["global_parameters"] = [{"name": "internal_cluster_id", "value": cluster_id}]
    defaults.update(sections)
    return _response(**defaults)


def _contract(cluster_id: str | None = None) -> RoutingContract:
    return RoutingContract(
        "Worker receives the empty key",
        NodeId(_VHOST, "events", NodeKind.EXCHANGE, cluster_id),
        "",
        (RoutingCondition.exactly({NodeId(_VHOST, "worker", NodeKind.QUEUE, cluster_id)}),),
    )


def _worker_observation(**fields: object) -> QueueResponse:
    return _observed_queue("worker", _VHOST, type="classic", arguments={"x-queue-type": "classic"}, **fields)


def test_identical_and_reordered_exports_have_equal_results() -> None:
    definitions = _capture(
        bindings=[
            _binding("events", "worker", vhost=_VHOST, arguments={"b": 1, "a": {"two": 2, "one": None}}),
            _binding("events", "worker", vhost=_VHOST, arguments={"a": {"one": None, "two": 9}, "b": 1}),
            _binding("events", "worker", "other", vhost=_VHOST),
        ],
    )
    reordered = definitions.model_dump(by_alias=True, exclude_unset=True)
    for key in ("queues", "exchanges", "bindings"):
        reordered[key].reverse()
    reordered["bindings"][-1]["arguments"] = {"a": {"one": None, "two": 2}, "b": 1}

    auditor = ClusterAuditor(definitions, queues=[], exchanges=[])
    other = ClusterAuditor(ClusterDefinitionsResponse.model_validate(reordered), queues=[], exchanges=[])
    contracts = [_contract()]
    same = auditor.compare(auditor, routing_contracts=contracts)

    assert compare_captures(auditor, other, routing_contracts=contracts) == same
    assert same.bindings == BindingComparison()
    assert not (same.queues.added or same.queues.removed or same.queues.attributes)
    assert same.routing_contracts[0].before == same.routing_contracts[0].after
    assert same.routing_contracts[0].before.verdict is RoutingVerdict.SATISFIED
    with pytest.raises(FrozenInstanceError):
        same.cluster_id = "another"  # type: ignore[misc]


def test_removed_parallel_binding_changes_contract_and_preserves_binding_identity() -> None:
    removed = _binding("events", "worker", vhost=_VHOST, arguments={"nested": {"enabled": None}})
    retained = _binding("events", "worker", "other", vhost=_VHOST, arguments={"nested": {"enabled": None}})
    # This parallel binding shares both endpoints and the empty key, but has
    # different arguments. Remove it too so the contract stops being satisfied.
    parallel = _binding("events", "worker", vhost=_VHOST, arguments={"nested": {"enabled": False}})
    before = ClusterAuditor(_capture(bindings=[parallel, retained, removed]))
    after = ClusterAuditor(_capture(bindings=[retained]))
    result = compare_captures(before, after, routing_contracts=[_contract(_CLUSTER)])

    assert result.bindings.added == ()
    assert result.bindings.removed == tuple(sorted(result.bindings.removed, key=edge_sort_key))
    assert [(edge.routing_key, edge.arguments) for edge in result.bindings.removed] == [
        ("", '{"nested": {"enabled": false}}'),
        ("", '{"nested": {"enabled": null}}'),
    ]
    (routing,) = result.routing_contracts
    assert routing.before.verdict is RoutingVerdict.SATISFIED
    assert routing.after.verdict is RoutingVerdict.VIOLATED
    assert routing.before.result is not None
    assert routing.after.result is not None
    assert routing.before.result.established_destinations == (NodeId(_VHOST, "worker", NodeKind.QUEUE, _CLUSTER),)
    assert routing.after.result.established_destinations == ()


def test_declaration_attributes_and_observed_policy_selections() -> None:
    regular = _policy("retention", r"^worker$", "queues", 10, vhost=_VHOST, definition={"message-ttl": 100})
    operator = {
        "component": "operator_policy",
        "vhost": _VHOST,
        "name": "operator",
        "value": {"pattern": r"^worker$", "apply-to": "queues", "definition": {"message-ttl": 200}, "priority": 0},
    }
    before = ClusterAuditor(
        _capture(policies=[regular], parameters=[operator]),
        queues=[_worker_observation(policy="retention", operator_policy=None)],
        exchanges=[],
    )
    after = ClusterAuditor(
        _capture(
            queues=[dict(_queue("worker", _VHOST, other=3), durable=False), _queue("new", _VHOST)],
            policies=[dict(regular, priority=11)],
            parameters=[operator],
        ),
        queues=[_worker_observation(policy=None, operator_policy="operator", durable=False)],
        exchanges=[],
    )
    result = before.compare(after)
    worker = NodeId(_VHOST, "worker", NodeKind.QUEUE, _CLUSTER)

    assert result.queues.added == (NodeId(_VHOST, "new", NodeKind.QUEUE, _CLUSTER),)
    assert result.queues.removed == (NodeId(_VHOST, "archive", NodeKind.QUEUE, _CLUSTER),)
    (attributes,) = result.queues.attributes
    assert attributes.identity == worker
    assert attributes.changed_fields == ("arguments", "durable")
    assert attributes.indeterminate_fields == ()
    assert result.regular_policies.attributes[0].changed_fields == ("priority",)
    assert [
        (row.before.resource, row.before.kind, row.before.policy_name, row.after.policy_name)
        for row in result.policy_selections.changed
    ] == [(worker, PolicyKind.REGULAR, "retention", None), (worker, PolicyKind.OPERATOR, None, "operator")]


def test_missing_section_and_selectors_are_indeterminate() -> None:
    data = _capture().model_dump(by_alias=True, exclude_unset=True)
    del data["bindings"]
    del data["queues"][0]["arguments"]
    before = ClusterAuditor(_capture(), queues=[_worker_observation(policy=None)], exchanges=[])
    after = ClusterAuditor(ClusterDefinitionsResponse.model_validate(data), queues=[], exchanges=[])
    report = before.compare(after, routing_contracts=[_contract()])

    assert report.bindings.indeterminate
    assert report.bindings.evidence.after == (ComparisonGap.BINDINGS_NOT_CAPTURED,)
    assert report.bindings.removed == ()
    assert report.queues.attributes[0].indeterminate_fields == ("arguments",)
    assert report.policy_selections.indeterminate
    assert report.policy_selections.changed == ()
    assert any(
        row.before.status is PolicySelectionStatus.NO_SELECTION
        and row.after.status is PolicySelectionStatus.NOT_CAPTURED
        for row in report.policy_selections.unavailable
    )
    (routing,) = report.routing_contracts
    assert routing.before.verdict is RoutingVerdict.SATISFIED
    assert routing.after.verdict is RoutingVerdict.UNDETERMINED
    assert routing.after.gaps == (ComparisonGap.BINDINGS_NOT_CAPTURED,)


def test_absent_arguments_suppress_only_the_ambiguous_binding_group() -> None:
    unknown = _binding("events", "worker", vhost=_VHOST)
    del unknown["arguments"]
    redacted = _binding("events", "archive", "opaque", vhost=_VHOST, arguments={"token": "example-secret"})
    before = ClusterAuditor(_capture(bindings=[_binding("events", "worker", vhost=_VHOST), redacted]))
    after = ClusterAuditor(_capture(bindings=[unknown, _binding("events", "archive", "new", vhost=_VHOST)]))

    report = compare_captures(before, after)

    assert report.bindings.indeterminate
    assert report.bindings.removed == ()
    assert [edge.routing_key for edge in report.bindings.added] == ["new"]


def test_missing_headers_arguments_affect_only_the_publication_that_uses_them() -> None:
    headers = _exchange("matching", _VHOST, "headers")
    direct = _exchange("events", _VHOST)
    main = _binding("events", "worker", vhost=_VHOST)
    known = _binding("matching", "worker", vhost=_VHOST)
    unknown = {field: value for field, value in known.items() if field != "arguments"}
    before = ClusterAuditor(_capture(exchanges=[direct, headers], bindings=[main, known]))
    after = ClusterAuditor(_capture(exchanges=[direct, headers], bindings=[main, unknown]))
    worker_contract = _contract()
    headers_contract = RoutingContract(
        "Header exchange reaches worker",
        NodeId(_VHOST, "matching", NodeKind.EXCHANGE),
        "",
        worker_contract.conditions,
    )

    headers_result, direct_result = before.compare(
        after, routing_contracts=[headers_contract, worker_contract]
    ).routing_contracts

    assert headers_result.before.verdict is RoutingVerdict.SATISFIED
    assert headers_result.after.verdict is RoutingVerdict.UNDETERMINED
    assert headers_result.after.result is not None
    assert headers_result.after.result.issues == (
        RoutingIssue(NodeId(_VHOST, "matching", NodeKind.EXCHANGE, _CLUSTER), RoutingUncertainty.INCOMPLETE_BINDING),
    )
    assert direct_result.before == direct_result.after


def test_supplemented_exchange_is_not_a_declaration_removal() -> None:
    before = ClusterAuditor(_capture(exchanges=[], bindings=[_binding("amq.direct", "worker", vhost=_VHOST)]))
    after = ClusterAuditor(_capture(exchanges=[], bindings=[]))
    assert before.topology.exchanges
    assert not after.topology.exchanges

    report = before.compare(after)

    assert not report.exchanges.removed
    assert report.bindings.removed[0].source.name == "amq.direct"


def test_different_projections_compare_only_the_shared_vhosts() -> None:
    before = ClusterAuditor(_capture(vhosts=[_vhost(_VHOST), _vhost("external")]))
    after = ClusterAuditor(_capture(bindings=[]))
    external = RoutingContract(
        "Outside projection",
        NodeId("external", "events", NodeKind.EXCHANGE),
        "",
        (RoutingCondition.exactly(set()),),
    )
    result = before.compare(after, after_vhosts={_VHOST}, routing_contracts=[_contract(), external])

    assert result.vhosts.removed == ()
    assert result.vhosts.indeterminate
    assert result.bindings.indeterminate
    assert len(result.bindings.removed) == 2
    assert result.routing_contracts[0].after.verdict is RoutingVerdict.VIOLATED
    assert result.routing_contracts[1].after.gaps == (ComparisonGap.OUTSIDE_SHARED_SCOPE,)


def test_missing_identity_needs_explicit_mapping_and_rebases_contract_results() -> None:
    before = ClusterAuditor(_capture(None))
    after = ClusterAuditor(_capture())
    with pytest.raises(TopologyAnalysisError, match="cluster_id mapping is required"):
        before.compare(after)
    with pytest.raises(TopologyAnalysisError, match="cluster_id mapping is required"):
        before.compare(before)
    with pytest.raises(TopologyAnalysisError, match="mapping conflicts"):
        before.compare(after, cluster_id="other")

    result = before.compare(after, cluster_id=_CLUSTER, routing_contracts=[_contract(_CLUSTER)])
    assert result.cluster_id == _CLUSTER
    assert result.bindings == BindingComparison()
    assert result.routing_contracts[0].before == result.routing_contracts[0].after
    assert before.cluster_id is None
    assert before.compare(before, cluster_id="caller-assigned", routing_contracts=[_contract()]).cluster_id == (
        "caller-assigned"
    )


@pytest.mark.parametrize("mapping", [None, _CLUSTER, "other"])
def test_conflicting_cluster_ids_are_rejected_even_with_mapping(mapping: str | None) -> None:
    before = ClusterAuditor(_capture())
    after = ClusterAuditor(_capture("rabbitmq-cluster-id-other"))
    with pytest.raises(TopologyAnalysisError, match="conflicting cluster IDs"):
        before.compare(after, cluster_id=mapping)


def test_topology_only_and_later_wire_model_mutation() -> None:
    definitions = _capture()
    before = ClusterAuditor(definitions)
    after = ClusterAuditor.from_topology(before.topology)
    later = ClusterAuditor(_capture(bindings=[]))
    expected = before.compare(later, routing_contracts=[_contract()])
    definitions.bindings.clear()
    definitions.exchanges.clear()

    assert expected.bindings.removed
    assert before.compare(later, routing_contracts=[_contract()]) == expected
    result = before.compare(after, routing_contracts=[_contract()])
    assert result.exchanges.evidence.after == (ComparisonGap.DEFINITIONS_NOT_CAPTURED,)
    assert result.bindings.removed == ()
    assert result.routing_contracts[0].after.gaps == (ComparisonGap.DEFINITIONS_NOT_CAPTURED,)
