"""Evaluate destination constraints against the public capture audit facade."""

import pickle
from dataclasses import replace
from unittest.mock import patch

import pytest
from tests.shared.parser_fixtures import _binding, _exchange, _observed_queue, _policy, _queue, _response, _vhost

from rabbitmq_management_sdk import ClusterAuditor, ExchangeResponse, TopologyAnalysisError
from rabbitmq_management_sdk.resources.v4.admin.schemas.export_response import ClusterDefinitionsResponse
from rabbitmq_management_sdk.topology import (
    DeadLetterCoverageStatus,
    DeadLetterRoutingIssue,
    DeadLetterUncertainty,
    EdgeKind,
    NodeId,
    NodeKind,
    RoutingCondition,
    RoutingContract,
    RoutingContractResult,
    RoutingIssue,
    RoutingOriginStatus,
    RoutingStatus,
    RoutingUncertainty,
    RoutingVerdict,
)
from rabbitmq_management_sdk.topology.routing import _ExchangeRouter

pytestmark = pytest.mark.unit

_ENTRY = NodeId("v", "entry", NodeKind.EXCHANGE)
_PLUGIN = replace(_ENTRY, name="plugin")
_HEADERS = replace(_ENTRY, name="headers")
_BILLING = NodeId("v", "billing", NodeKind.QUEUE)
_ANALYTICS = replace(_BILLING, name="analytics")
_BACKUP = replace(_BILLING, name="backup")


def _contract(*conditions: RoutingCondition, key: str | None = "invoice") -> RoutingContract:
    return RoutingContract("invoice routing", _ENTRY, key, conditions)


def _evaluate(auditor: ClusterAuditor, contract: RoutingContract) -> RoutingContractResult:
    (result,) = auditor.evaluate_routing_contracts([contract])
    assert tuple(item.condition for item in result.conditions) == contract.conditions
    return result


def _billing_auditor(*, analytics: bool = False, plugin: bool = False) -> ClusterAuditor:
    bindings = [_binding("entry", "billing", "invoice")]
    if analytics:
        bindings.append(_binding("entry", "analytics", "invoice"))
    if plugin:
        bindings.append(_binding("entry", "plugin", "invoice", destination_type="exchange"))
    return ClusterAuditor(
        _response(
            vhosts=[_vhost("v")],
            exchanges=[_exchange("entry", "v"), _exchange("plugin", "v", "x-plugin")],
            queues=[_queue(name, "v") for name in ("billing", "analytics", "backup")],
            bindings=bindings,
        )
    )


@pytest.mark.parametrize("plugin", [False, True])
def test_established_extra_destination_violates_exclusivity_even_with_an_open_branch(plugin: bool) -> None:
    auditor = _billing_auditor(analytics=True, plugin=plugin)
    result = _evaluate(
        auditor,
        _contract(
            RoutingCondition.reaches_all({_BILLING}),
            RoutingCondition.reaches_all({_BILLING, _ANALYTICS}),
            RoutingCondition.reaches_any({_BACKUP, _ANALYTICS}),
            RoutingCondition.avoids({_BILLING}),
            RoutingCondition.only_within({_BILLING}),
            RoutingCondition.exactly({_BILLING}),
        ),
    )

    assert result.established_destinations == (_ANALYTICS, _BILLING)
    assert result.conditional_destinations == ()
    assert result.open_branch is plugin
    assert [item.verdict for item in result.conditions] == [
        RoutingVerdict.SATISFIED,
        RoutingVerdict.SATISFIED,
        RoutingVerdict.SATISFIED,
        RoutingVerdict.VIOLATED,
        RoutingVerdict.VIOLATED,
        RoutingVerdict.VIOLATED,
    ]
    assert result.verdict == RoutingVerdict.VIOLATED


def test_any_with_allowlist_permits_both_approved_destinations() -> None:
    auditor = _billing_auditor(analytics=True)
    approved = {_BILLING, _ANALYTICS}
    result = _evaluate(
        auditor,
        _contract(
            RoutingCondition.reaches_any(approved),
            RoutingCondition.only_within(approved),
            RoutingCondition.avoids({_BACKUP}),
        ),
    )
    assert result.established_destinations == (_ANALYTICS, _BILLING)
    assert all(item.verdict == RoutingVerdict.SATISFIED for item in result.conditions)
    assert result.verdict == RoutingVerdict.SATISFIED


def test_open_branch_leaves_exclusion_and_unestablished_destinations_uncertain() -> None:
    auditor = _billing_auditor(plugin=True)
    reaches_billing = RoutingCondition.reaches_all({_BILLING})
    only_billing = RoutingCondition.exactly({_BILLING})
    result = _evaluate(
        auditor,
        _contract(
            reaches_billing,
            only_billing,
            RoutingCondition.reaches_all({_BILLING, _ANALYTICS}),
            RoutingCondition.reaches_any({_ANALYTICS}),
            RoutingCondition.avoids({_ANALYTICS}),
            RoutingCondition.only_within({_BILLING}),
            RoutingCondition.avoids(set()),
        ),
    )

    assert result.established_destinations == (_BILLING,)
    assert result.conditional_destinations == ()
    assert result.open_branch
    assert result.issues == (RoutingIssue(_PLUGIN, RoutingUncertainty.UNSUPPORTED_ROUTING_SEMANTICS),)
    assert result.verdict == RoutingVerdict.UNDETERMINED
    assert [item.verdict for item in result.conditions] == [
        RoutingVerdict.SATISFIED,
        RoutingVerdict.UNDETERMINED,
        RoutingVerdict.UNDETERMINED,
        RoutingVerdict.UNDETERMINED,
        RoutingVerdict.UNDETERMINED,
        RoutingVerdict.UNDETERMINED,
        RoutingVerdict.SATISFIED,
    ]
    assert _evaluate(auditor, _contract(reaches_billing)).verdict == RoutingVerdict.SATISFIED
    assert _evaluate(auditor, _contract(only_billing)).verdict == RoutingVerdict.UNDETERMINED
    assert (
        _evaluate(auditor, _contract(only_billing, RoutingCondition.avoids({_BILLING}))).verdict
        == RoutingVerdict.VIOLATED
    )


@pytest.mark.parametrize("target", ["billing", "analytics", "dead-end"])
def test_conditional_headers_keep_only_their_possible_queue_destinations(target: str) -> None:
    auditor = ClusterAuditor(
        _response(
            vhosts=[_vhost("v")],
            queues=[_queue("billing", "v"), _queue("analytics", "v")],
            exchanges=[_exchange("entry", "v"), _exchange("headers", "v", "headers"), _exchange("dead-end", "v")],
            bindings=[
                _binding("entry", "billing", "invoice"),
                _binding("entry", "headers", "invoice", destination_type="exchange"),
                _binding(
                    "headers",
                    target,
                    destination_type="exchange" if target == "dead-end" else "queue",
                    arguments={"region": "EU"},
                ),
            ],
        )
    )
    result = _evaluate(
        auditor,
        _contract(
            RoutingCondition.exactly({_BILLING}),
            RoutingCondition.only_within({_BILLING}),
            RoutingCondition.avoids({_ANALYTICS}),
            RoutingCondition.reaches_any({_ANALYTICS}),
        ),
    )

    assert result.established_destinations == (_BILLING,)
    assert not result.open_branch
    assert result.issues == (RoutingIssue(_HEADERS, RoutingUncertainty.MESSAGE_HEADERS_NOT_CAPTURED),)
    if target == "analytics":
        assert result.conditional_destinations == (_ANALYTICS,)
        assert all(item.verdict == RoutingVerdict.UNDETERMINED for item in result.conditions)
    else:
        assert result.conditional_destinations == ()
        assert [item.verdict for item in result.conditions] == [
            RoutingVerdict.SATISFIED,
            RoutingVerdict.SATISFIED,
            RoutingVerdict.SATISFIED,
            RoutingVerdict.VIOLATED,
        ]


def test_alternate_activation_empty_key_and_established_no_destinations() -> None:
    auditor = ClusterAuditor(
        _response(
            vhosts=[_vhost("v")],
            queues=[_queue("billing", "v")],
            exchanges=[
                _exchange("entry", "v", **{"alternate-exchange": "fallback"}),
                _exchange("fallback", "v", "fanout"),
                _exchange("dead-end", "v", "fanout"),
            ],
            bindings=[
                _binding("entry", "dead-end", "", destination_type="exchange"),
                _binding("fallback", "billing"),
            ],
        )
    )
    conditions = (
        RoutingCondition.reaches_all({_BILLING}),
        RoutingCondition.reaches_any({_BILLING}),
        RoutingCondition.exactly(set()),
        RoutingCondition.only_within(set()),
        RoutingCondition.only_within({_BILLING}),
        RoutingCondition.avoids({_BILLING}),
    )

    active, suppressed, unspecified = auditor.evaluate_routing_contracts(
        [_contract(*conditions, key=key) for key in ("unmapped", "", None)]
    )

    assert active.established_destinations == (_BILLING,)
    assert [item.verdict for item in active.conditions] == [RoutingVerdict.SATISFIED] * 2 + [
        RoutingVerdict.VIOLATED,
        RoutingVerdict.VIOLATED,
        RoutingVerdict.SATISFIED,
        RoutingVerdict.VIOLATED,
    ]
    assert suppressed.contract.routing_key == ""
    assert suppressed.origin_status == RoutingOriginStatus.EVALUATED
    assert suppressed.established_destinations == suppressed.conditional_destinations == ()
    assert [item.verdict for item in suppressed.conditions] == (
        [RoutingVerdict.VIOLATED] * 2 + [RoutingVerdict.SATISFIED] * 4
    )
    assert unspecified.conditional_destinations == (_BILLING,)
    assert unspecified.issues == (RoutingIssue(_ENTRY, RoutingUncertainty.ROUTING_KEY_NOT_CAPTURED),)
    assert unspecified.conditions[0].verdict == RoutingVerdict.UNDETERMINED
    assert unspecified.conditions[2].verdict == RoutingVerdict.UNDETERMINED
    assert unspecified.conditions[4].verdict == RoutingVerdict.SATISFIED


def test_uncaptured_alternate_policy_affects_contract_and_coverage() -> None:
    entry = _exchange("entry", "v")
    fallback = _exchange("fallback", "v", "fanout")
    definitions = _response(
        vhosts=[_vhost("v")],
        queues=[
            _queue("billing", "v"),
            _queue(
                "unmatched-work", "v", **{"x-dead-letter-exchange": "entry", "x-dead-letter-routing-key": "invoice"}
            ),
            _queue("matched-work", "v", **{"x-dead-letter-exchange": "entry", "x-dead-letter-routing-key": "mapped"}),
        ],
        exchanges=[entry, fallback],
        policies=[
            _policy("reroute", "^entry$", "exchanges", 0, vhost="v", definition={"alternate-exchange": "fallback"})
        ],
        bindings=[_binding("entry", "billing", "mapped"), _binding("fallback", "billing")],
    )
    queues = [
        _observed_queue(name, "v", type="classic", policy=None, arguments={"x-queue-type": "classic"})
        for name in ("billing", "unmatched-work", "matched-work")
    ]
    fallback_observation = ExchangeResponse.model_validate({**fallback, "policy": None})
    contract = _contract(RoutingCondition.exactly(set()))

    omitted = ClusterAuditor(
        definitions,
        queues=queues,
        exchanges=[ExchangeResponse.model_validate(entry), fallback_observation],
    )
    uncertain = _evaluate(omitted, contract)
    assert uncertain.verdict == RoutingVerdict.UNDETERMINED
    assert uncertain.open_branch
    assert uncertain.issues == (RoutingIssue(_ENTRY, RoutingUncertainty.POLICY_SELECTION_NOT_CAPTURED),)
    report = omitted.audit(routing_contracts=[contract])
    assert report.routing_contracts == (uncertain,)
    exchange_coverage = omitted.exchange_routing_coverage(routing_key="invoice")
    exchange = next(row for row in exchange_coverage if row.exchange.id == _ENTRY)
    assert exchange.status == RoutingStatus.UNDETERMINED
    assert exchange.issues == uncertain.issues
    dead_letters = {row.queue.id.name: row for row in omitted.dead_letter_coverage()}
    unmatched_dlx = dead_letters["unmatched-work"]
    assert unmatched_dlx.status == DeadLetterCoverageStatus.UNDETERMINED
    dlx_issue = DeadLetterRoutingIssue(_ENTRY, DeadLetterUncertainty.POLICY_SELECTION_NOT_CAPTURED)
    assert unmatched_dlx.issues == (dlx_issue,)
    assert unmatched_dlx in report.dead_letter_coverage

    restored = ClusterAuditor.from_topology(pickle.loads(pickle.dumps(omitted.topology)))
    assert _evaluate(restored, contract) == uncertain

    matched = _evaluate(omitted, _contract(RoutingCondition.exactly({_BILLING}), key="mapped"))
    assert matched.verdict == RoutingVerdict.SATISFIED
    assert not matched.issues
    matched_dlx = dead_letters["matched-work"]
    assert matched_dlx.status == DeadLetterCoverageStatus.DESTINATION_POSSIBLE
    assert matched_dlx.destinations == (_BILLING,)
    assert not matched_dlx.issues

    no_selection = ClusterAuditor(
        definitions,
        queues=queues,
        exchanges=[ExchangeResponse.model_validate({**entry, "policy": None}), fallback_observation],
    )
    assert _evaluate(no_selection, contract).verdict == RoutingVerdict.SATISFIED
    no_selection_dlx = next(row for row in no_selection.dead_letter_coverage() if row.queue.id.name == "unmatched-work")
    assert no_selection_dlx.status == DeadLetterCoverageStatus.NO_DESTINATION
    assert not no_selection_dlx.issues

    selected = ClusterAuditor(
        definitions,
        queues=queues,
        exchanges=[ExchangeResponse.model_validate({**entry, "policy": "reroute"}), fallback_observation],
    )
    result = _evaluate(selected, contract)
    assert result.verdict == RoutingVerdict.VIOLATED
    assert result.established_destinations == (_BILLING,)


@pytest.mark.parametrize(
    ("policy_section", "policy_observation", "expected"),
    [
        pytest.param({}, None, RoutingVerdict.UNDETERMINED, id="no-policy-section-or-exchange-observation"),
        pytest.param({}, {}, RoutingVerdict.UNDETERMINED, id="no-policy-section-or-selector"),
        pytest.param({}, {"policy": None}, RoutingVerdict.SATISFIED, id="explicit-null-selector"),
        pytest.param({"policies": []}, None, RoutingVerdict.SATISFIED, id="explicit-empty-section"),
        pytest.param({"policies": []}, {}, RoutingVerdict.SATISFIED, id="explicit-empty-section-with-observation"),
    ],
)
def test_omitted_policy_section_does_not_establish_discard(
    policy_section: dict[str, object], policy_observation: dict[str, object] | None, expected: RoutingVerdict
) -> None:
    entry = _exchange("entry", "v")
    definitions = ClusterDefinitionsResponse.model_validate(
        {
            "vhosts": [_vhost("v")],
            "queues": [_queue("billing", "v")],
            "exchanges": [entry],
            **policy_section,
        }
    )
    if policy_observation is None:
        auditor = ClusterAuditor(definitions)
    else:
        billing = _observed_queue("billing", "v", type="classic", arguments={"x-queue-type": "classic"})
        auditor = ClusterAuditor(
            definitions, queues=[billing], exchanges=[ExchangeResponse.model_validate({**entry, **policy_observation})]
        )

    result = _evaluate(auditor, _contract(RoutingCondition.exactly(set())))
    assert result.verdict == expected
    assert result.issues == (
        (RoutingIssue(_ENTRY, RoutingUncertainty.POLICY_SELECTION_NOT_CAPTURED),)
        if expected == RoutingVerdict.UNDETERMINED
        else ()
    )


@pytest.mark.parametrize(
    ("observation", "expected"),
    [
        pytest.param(None, RoutingVerdict.UNDETERMINED, id="no-observation"),
        pytest.param({"policy": None}, RoutingVerdict.UNDETERMINED, id="omitted-observed-arguments"),
        pytest.param({"policy": "reroute"}, RoutingVerdict.UNDETERMINED, id="named-policy-without-arguments"),
        pytest.param({"policy": None, "arguments": {}}, RoutingVerdict.SATISFIED, id="empty-observed-arguments"),
    ],
)
def test_omitted_exchange_arguments_do_not_establish_discard(
    observation: dict[str, object] | None, expected: RoutingVerdict
) -> None:
    entry = _exchange("entry", "v")
    del entry["arguments"]
    discard = _exchange("discard", "v", "fanout")
    policies = []
    if observation is not None and observation.get("policy") == "reroute":
        policies.append(
            _policy("reroute", "^entry$", "exchanges", 0, vhost="v", definition={"alternate-exchange": "discard"})
        )
    definitions = _response(
        vhosts=[_vhost("v")],
        queues=[_queue("billing", "v")],
        exchanges=[entry, discard],
        bindings=[_binding("entry", "billing", "mapped")],
        policies=policies,
    )
    if observation is None:
        auditor = ClusterAuditor(definitions)
    else:
        billing = _observed_queue("billing", "v", type="classic", arguments={"x-queue-type": "classic"})
        auditor = ClusterAuditor(
            definitions,
            queues=[billing],
            exchanges=[
                ExchangeResponse.model_validate({**entry, **observation}),
                ExchangeResponse.model_validate({**discard, "policy": None}),
            ],
        )

    contract = _contract(RoutingCondition.exactly(set()))
    result = _evaluate(auditor, contract)
    assert result.verdict == expected
    assert result.open_branch is (expected == RoutingVerdict.UNDETERMINED)
    assert result.established_destinations == result.conditional_destinations == ()
    assert result.issues == (
        (RoutingIssue(_ENTRY, RoutingUncertainty.EXCHANGE_ARGUMENTS_NOT_CAPTURED),)
        if expected == RoutingVerdict.UNDETERMINED
        else ()
    )
    assert all(edge.kind != EdgeKind.ALTERNATE_EXCHANGE for edge in auditor.topology.edges)
    coverage = next(
        row for row in auditor.exchange_routing_coverage(routing_key="invoice") if row.exchange.id == _ENTRY
    )
    assert coverage.status == (
        RoutingStatus.UNDETERMINED if expected == RoutingVerdict.UNDETERMINED else RoutingStatus.NO_DESTINATION
    )
    assert coverage.issues == result.issues
    restored = ClusterAuditor.from_topology(pickle.loads(pickle.dumps(auditor.topology)))
    assert _evaluate(restored, contract) == result

    matched = _evaluate(auditor, _contract(RoutingCondition.exactly({_BILLING}), key="mapped"))
    assert matched.verdict == RoutingVerdict.SATISFIED
    assert matched.established_destinations == (_BILLING,)
    assert not matched.open_branch
    assert not matched.issues


@pytest.mark.parametrize(
    ("arguments_captured", "expected_target", "expected"),
    [
        pytest.param(False, "fallback", RoutingVerdict.VIOLATED, id="observed-direct-alternate"),
        pytest.param(True, "discard", RoutingVerdict.SATISFIED, id="empty-definition-arguments"),
    ],
)
def test_observed_exchange_arguments_fill_omitted_definition_map(
    arguments_captured: bool, expected_target: str, expected: RoutingVerdict
) -> None:
    entry = _exchange("entry", "v")
    fallback = _exchange("fallback", "v", "fanout")
    discard = _exchange("discard", "v", "fanout")
    if not arguments_captured:
        del entry["arguments"]
    definitions = _response(
        vhosts=[_vhost("v")],
        queues=[_queue("billing", "v")],
        exchanges=[entry, fallback, discard],
        bindings=[_binding("fallback", "billing")],
        policies=[
            _policy("reroute", "^entry$", "exchanges", 0, vhost="v", definition={"alternate-exchange": "discard"})
        ],
    )
    billing = _observed_queue("billing", "v", type="classic", arguments={"x-queue-type": "classic"})
    observation = ExchangeResponse.model_validate(
        {**entry, "policy": "reroute", "arguments": {"alternate-exchange": "fallback"}}
    )
    auditor = ClusterAuditor(
        definitions,
        queues=[billing],
        exchanges=[
            observation,
            ExchangeResponse.model_validate({**fallback, "policy": None}),
            ExchangeResponse.model_validate({**discard, "policy": None}),
        ],
    )

    result = _evaluate(auditor, _contract(RoutingCondition.exactly(set())))
    assert result.verdict == expected
    assert result.established_destinations == (() if arguments_captured else (_BILLING,))
    assert not result.issues
    (alternate,) = (edge for edge in auditor.topology.edges if edge.kind == EdgeKind.ALTERNATE_EXCHANGE)
    assert alternate.source == _ENTRY
    assert alternate.target == replace(_ENTRY, name=expected_target)


def test_scope_duplicate_paths_and_missing_and_internal_origins() -> None:
    internal = replace(_ENTRY, name="internal")
    missing = replace(_ENTRY, name="missing")
    elsewhere = replace(_BILLING, vhost="other")
    auditor = ClusterAuditor(
        _response(
            vhosts=[_vhost("v"), _vhost("other")],
            exchanges=[
                _exchange("entry", "v"),
                _exchange("entry", "other"),
                {**_exchange("internal", "v", "fanout"), "internal": True},
            ],
            queues=[_queue("billing", "v"), _queue("billing", "other")],
            bindings=[
                _binding("entry", "billing", "invoice"),
                _binding("entry", "internal", "invoice", destination_type="exchange"),
                _binding("entry", "internal", "via-internal", destination_type="exchange"),
                _binding("internal", "billing"),
                _binding("entry", "billing", "invoice", vhost="other"),
            ],
        )
    )
    local = _contract(RoutingCondition.exactly({_BILLING}), RoutingCondition.avoids({elsewhere}))
    expect_empty = _contract(RoutingCondition.exactly(set()))
    contracts = [
        local,
        replace(local, routing_key="via-internal"),
        replace(local, origin=replace(_ENTRY, vhost="other")),
        replace(expect_empty, origin=missing),
        replace(local, origin=internal),
    ]
    source = ClusterAuditor.from_topology(pickle.loads(pickle.dumps(auditor.topology)))
    first, via_internal, other, absent, denied = source.evaluate_routing_contracts(contracts)

    assert first.established_destinations == (_BILLING,)
    assert first.verdict == RoutingVerdict.SATISFIED
    assert via_internal.established_destinations == (_BILLING,)
    assert via_internal.verdict == RoutingVerdict.SATISFIED
    assert other.established_destinations == (elsewhere,)
    assert other.verdict == RoutingVerdict.VIOLATED
    assert absent.origin_status == RoutingOriginStatus.MISSING
    assert absent.verdict == RoutingVerdict.UNDETERMINED
    assert absent.issues == (RoutingIssue(missing, RoutingUncertainty.MISSING_RESOURCE),)
    assert denied.origin_status == RoutingOriginStatus.INTERNAL
    assert denied.verdict == RoutingVerdict.VIOLATED
    assert denied.established_destinations == ()
    assert next(
        item for item in source.exchange_routing_coverage(routing_key="invoice") if item.exchange.id == internal
    ).destinations == (_BILLING,)
    assert source.evaluate_routing_contracts(contracts) == auditor.evaluate_routing_contracts(contracts)


def test_open_branch_cannot_supply_a_queue_from_another_vhost_or_cluster() -> None:
    auditor = _billing_auditor(plugin=True)
    elsewhere = {replace(_BILLING, vhost="other"), replace(_BILLING, cluster_id="other")}
    result = _evaluate(
        auditor,
        _contract(
            RoutingCondition.avoids(elsewhere),
            RoutingCondition.reaches_any(elsewhere),
            RoutingCondition.reaches_all(elsewhere | {_BILLING}),
            RoutingCondition.exactly(elsewhere | {_BILLING}),
        ),
    )
    assert [item.verdict for item in result.conditions] == [RoutingVerdict.SATISFIED] + [RoutingVerdict.VIOLATED] * 3


def test_batch_shares_traces_and_aggregate_evaluation_is_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    auditor = _billing_auditor()
    contract = _contract(RoutingCondition.reaches_all({_BILLING}))
    contracts = [contract, replace(contract, name="other"), replace(contract, name="empty", routing_key="")]
    traced = []
    original_trace = _ExchangeRouter._trace

    def track_trace(router: _ExchangeRouter, origin: NodeId, key: str | None) -> object:
        traced.append((origin, key))
        return original_trace(router, origin, key)

    monkeypatch.setattr(_ExchangeRouter, "_trace", track_trace)
    batch = auditor.evaluate_routing_contracts(contracts)
    assert [item.contract for item in batch] == contracts
    assert traced == [(_ENTRY, "invoice"), (_ENTRY, "")]
    assert pickle.loads(pickle.dumps(batch)) == batch
    assert auditor.audit(routing_contracts=contracts).routing_contracts == batch
    with patch.object(auditor, "evaluate_routing_contracts", side_effect=AssertionError):
        assert auditor.audit().routing_contracts == ()


def test_empty_batch_performs_no_routing_and_invalid_batch_raises_sdk_error() -> None:
    auditor = _billing_auditor()
    with patch("rabbitmq_management_sdk.topology.routing_contracts._ExchangeRouter", side_effect=AssertionError):
        assert auditor.evaluate_routing_contracts([]) == ()
        with pytest.raises(TopologyAnalysisError, match="sequence of RoutingContract"):
            auditor.evaluate_routing_contracts(["invalid"])  # type: ignore[list-item]
