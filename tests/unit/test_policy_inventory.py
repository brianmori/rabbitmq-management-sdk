"""Exercise the public policy inventory without approximating broker matching."""

import pickle
from dataclasses import replace

import pytest
from tests.shared.parser_fixtures import _exchange, _observed_queue, _policy, _queue, _response, _vhost

from rabbitmq_management_sdk import ClusterAuditor, TopologyAnalysisError, TopologyParseError
from rabbitmq_management_sdk.resources.v4.exchanges.schemas.exchange_response import ExchangeResponse
from rabbitmq_management_sdk.topology import (
    ClusterTopology,
    NodeId,
    NodeKind,
    PolicyDeclaration,
    PolicyId,
    PolicyKind,
    PolicySelection,
    PolicySelectionStatus,
    QueueNode,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("version", ["4.2.9", "4.3.8"])
def test_named_null_and_omitted_selection_are_distinct(version: str) -> None:
    sentinel = "POLICY_PAYLOAD_MUST_NOT_BE_RETAINED"
    definition = _response(
        rabbitmq_version=version,
        global_parameters=[{"name": "internal_cluster_id", "value": "broker-id"}],
        vhosts=[_vhost("v", "quorum")],
        queues=[_queue(name, "v") for name in ("c", "b", "a")],
        policies=[
            _policy(
                "retention",
                "(?<broker_pattern>ignored)",
                "quorum_queues",
                10,
                vhost="v",
                definition={"plugin-setting": sentinel},
            )
        ],
    )
    auditor = ClusterAuditor(
        definition,
        queues=[
            _observed_queue("c", "v", effective_policy_definition={"plugin-setting": sentinel}),
            _observed_queue("b", "v", policy=None),
            _observed_queue("a", "v", policy="retention"),
        ],
        exchanges=[],
    )
    identity = PolicyId("v", "retention", PolicyKind.REGULAR, "broker-id")
    a, b, c = (NodeId("v", name, NodeKind.QUEUE, "broker-id") for name in ("a", "b", "c"))
    coverage = auditor.policy_lookup(identity)
    assert coverage is not None
    assert coverage.declaration == PolicyDeclaration(identity, "(?<broker_pattern>ignored)", "quorum_queues", 10)
    assert coverage.selected_resources == (a,)
    inventory = auditor.policy_inventory()
    assert tuple(row for row in inventory.selections if row.kind is PolicyKind.REGULAR) == (
        PolicySelection(a, PolicyKind.REGULAR, PolicySelectionStatus.SELECTED, "retention"),
        PolicySelection(b, PolicyKind.REGULAR, PolicySelectionStatus.NO_SELECTION),
        PolicySelection(c, PolicyKind.REGULAR, PolicySelectionStatus.NOT_CAPTURED),
    )
    assert tuple(row.resource for row in inventory.missing_selections if row.kind is PolicyKind.REGULAR) == (c,)
    assert len(inventory.missing_selections) == 4
    assert auditor.audit().policy_inventory == inventory
    assert auditor.policy_lookup(replace(identity, cluster_id="other")) is None
    assert auditor.policy_lookup(replace(identity, kind=PolicyKind.OPERATOR)) is None
    with pytest.raises(TopologyAnalysisError, match="must be PolicyId"):
        auditor.policy_lookup("retention")  # type: ignore[arg-type]

    serialized = pickle.dumps(auditor.topology)
    assert sentinel.encode() not in serialized
    assert sentinel not in repr(inventory)
    transferred = ClusterAuditor.from_topology(pickle.loads(serialized))
    assert transferred.policy_lookup(identity) == coverage
    assert transferred.policy_inventory() == inventory
    assert not transferred.topology.edges


def test_same_names_and_both_policy_fields_stay_independent_and_ordered() -> None:
    def operator(vhost: str, name: str, apply_to: str) -> dict[str, object]:
        return {
            "component": "operator_policy",
            "name": name,
            "vhost": vhost,
            "value": {"pattern": ".*", "priority": 5, "apply-to": apply_to, "definition": {}},
        }

    policies = [_policy("shared", ".*", "all", 10, vhost=vhost) for vhost in ("v2", "v1")]
    parameters = [
        operator("v2", "shared", "queues"),
        operator("v1", "shared", "queues"),
        operator("v1", "exchange-op", "exchanges"),
    ]
    queues = [_queue("same", "v2"), _queue("same", "v1"), _queue("before", "v1")]
    exchanges = [_exchange("same", "v1"), _exchange("none", "v2")]
    observed_queues = [
        _observed_queue("same", "v2", policy="shared", operator_policy=None),
        _observed_queue("same", "v1", policy="shared", operator_policy="shared"),
        _observed_queue("before", "v1", policy="shared", operator_policy="shared"),
    ]
    observed_exchanges = [
        ExchangeResponse.model_validate({**exchanges[0], "policy": "shared", "operator_policy": "exchange-op"}),
        ExchangeResponse.model_validate(exchanges[1]),
    ]

    def auditor(*, reverse: bool) -> ClusterAuditor:
        step = -1 if reverse else 1
        return ClusterAuditor(
            _response(
                rabbitmq_version="4.3.8",
                vhosts=[_vhost("v1", "quorum"), _vhost("v2", "quorum")],
                policies=policies[::step],
                parameters=parameters[::step],
                queues=queues[::step],
                exchanges=exchanges[::step],
            ),
            queues=observed_queues[::step],
            exchanges=observed_exchanges[::step],
        )

    inventory = auditor(reverse=False).policy_inventory()
    assert inventory == auditor(reverse=True).policy_inventory()
    before = NodeId("v1", "before", NodeKind.QUEUE)
    queue = NodeId("v1", "same", NodeKind.QUEUE)
    exchange = NodeId("v1", "same", NodeKind.EXCHANGE)
    assert [(row.declaration.id, row.selected_resources) for row in inventory.policies] == [
        (PolicyId("v1", "exchange-op", PolicyKind.OPERATOR), (exchange,)),
        (PolicyId("v1", "shared", PolicyKind.OPERATOR), (before, queue)),
        (PolicyId("v1", "shared", PolicyKind.REGULAR), (before, exchange, queue)),
        (PolicyId("v2", "shared", PolicyKind.OPERATOR), ()),
        (PolicyId("v2", "shared", PolicyKind.REGULAR), (NodeId("v2", "same", NodeKind.QUEUE),)),
    ]
    assert len(inventory.missing_selections) == 2
    assert {row.resource for row in inventory.missing_selections} == {NodeId("v2", "none", NodeKind.EXCHANGE)}


def test_observed_only_resources_keep_unresolved_names_without_adding_nodes() -> None:
    auditor = ClusterAuditor(
        _response(),
        queues=[_observed_queue("temporary", "v", policy="outside-capture")],
        exchanges=[],
    )

    inventory = auditor.policy_inventory()
    assert not inventory.policies
    assert not auditor.topology.nodes
    regular, operator = inventory.selections
    assert regular.policy_id == PolicyId("v", "outside-capture", PolicyKind.REGULAR)
    assert regular.status is PolicySelectionStatus.SELECTED
    assert operator.status is PolicySelectionStatus.NOT_CAPTURED


def test_absent_observations_leave_both_policy_kinds_unknown() -> None:
    auditor = ClusterAuditor(
        _response(
            vhosts=[_vhost("v")],
            queues=[_queue("q", "v")],
            policies=[_policy("present", ".*", "queues", 0, vhost="v")],
        )
    )
    (coverage,) = auditor.policy_inventory().policies
    assert coverage.selected_resources == ()
    assert len(auditor.policy_inventory().missing_selections) == 2
    queue = QueueNode(NodeId("v", "q", NodeKind.QUEUE), "classic", True)
    manual = ClusterAuditor.from_topology(ClusterTopology(frozenset(), frozenset({queue}), frozenset(), frozenset()))
    assert not manual.policy_inventory().policies
    assert manual.policy_inventory().selections == auditor.policy_inventory().selections


def test_unselected_duplicate_declarations_cannot_resolve_a_lookup() -> None:
    auditor = ClusterAuditor(_response(policies=[_policy("shared", pattern, "queues", 0) for pattern in ("z", "a")]))
    assert [row.declaration.pattern for row in auditor.policy_inventory().policies] == ["a", "z"]
    assert auditor.policy_lookup(PolicyId("t", "shared", PolicyKind.REGULAR)) is None


def test_inventory_preserves_routing_selection_requirements_and_validation() -> None:
    definition = _response(
        vhosts=[_vhost("v", "quorum")],
        queues=[_queue("q", "v")],
        policies=[_policy("dlx", ".*", "queues", 1, vhost="v", definition={"dead-letter-exchange": "dead"})],
    )
    with pytest.raises(TopologyParseError, match="routing-relevant user policies"):
        ClusterAuditor(definition)
    with pytest.raises(TopologyParseError, match="does not identify exactly one policy"):
        ClusterAuditor(definition, queues=[_observed_queue("q", "v", policy="absent")], exchanges=[])
    # The existing routing adapter accepts an observation with an omitted
    # field as sufficient to build the graph. The inventory reports the gap.
    auditor = ClusterAuditor(definition, queues=[_observed_queue("q", "v")], exchanges=[])
    assert not auditor.topology.edges
    assert len(auditor.policy_inventory().missing_selections) == 2
