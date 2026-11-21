"""
Tests for models.py -- the frozen-dataclass domain layer. Previously
only exercised indirectly through parser.py's end-to-end test; this
file pins down the domain layer's own behavior independent of parsing:
NodeId identity, the id.kind consistency guard on each node type,
ShovelNode.is_cross_vhost's three-state logic, TopologyEdge equality
(including the new `arguments` field), and ClusterTopology's
id-collision guard.
"""

import pickle
from dataclasses import FrozenInstanceError, replace

import pytest

from rabbitmq_management_sdk import RabbitMQError, TopologyValidationError
from rabbitmq_management_sdk.topology import (
    ClusterTopology,
    CompetingShovelConsumers,
    ConfigurationEvidence,
    ConfigurationSource,
    ConfigurationStatus,
    ConfigurationUncertainty,
    ConfigurationValue,
    DeadLetterCoverage,
    DeadLetterCoverageStatus,
    DeadLetterRoutingIssue,
    DeadLetterUncertainty,
    EdgeKind,
    EndpointAuthority,
    ExchangeNode,
    ExchangeRoutingCoverage,
    FanoutExchangeBindings,
    FanoutQueue,
    NodeId,
    NodeKind,
    QueueLimitsConfiguration,
    QueueNode,
    QueueTypeGroup,
    QueueTypeInventory,
    QuorumConfigurationIssue,
    QuorumDeliveryConfiguration,
    QuorumPlacement,
    QuorumQueueConfiguration,
    ResourceEndpoint,
    RoutingCondition,
    RoutingConditionKind,
    RoutingContract,
    RoutingIssue,
    RoutingStatus,
    RoutingUncertainty,
    ShovelNode,
    TopologyEdge,
    TopologyNode,
    VhostQueueInventory,
    VirtualHostMetadata,
)

pytestmark = pytest.mark.unit


def _exchange(
    vhost: str,
    name: str,
    exchange_type: str = "direct",
    internal: bool = False,
    durable: bool = True,
) -> ExchangeNode:
    return ExchangeNode(
        id=NodeId(vhost=vhost, name=name, kind=NodeKind.EXCHANGE),
        exchange_type=exchange_type,
        internal=internal,
        durable=durable,
    )


def _queue(vhost: str, name: str, queue_type: str = "classic", durable: bool = True) -> QueueNode:
    return QueueNode(
        id=NodeId(vhost=vhost, name=name, kind=NodeKind.QUEUE),
        queue_type=queue_type,
        durable=durable,
    )


def _endpoint(
    vhost: str | None,
    *,
    is_confirmed_local: bool = False,
    name: str = "queue",
) -> ResourceEndpoint:
    return ResourceEndpoint(
        protocol="local",
        authorities=(),
        vhost=vhost,
        resource_name=name,
        resource_kind=NodeKind.QUEUE,
        routing_key=None,
        is_confirmed_local=is_confirmed_local,
    )


class TestRoutingContract:
    def test_inputs_are_frozen_and_pickleable(self) -> None:
        exchange = _exchange("v", "entry").id
        queue = _queue("v", "billing").id
        selected = {queue}
        condition = RoutingCondition.reaches_any(selected)
        contract = RoutingContract("invoice", exchange, "", (condition,))
        selected.clear()

        assert contract.conditions[0].queues == frozenset({queue})
        assert contract == pickle.loads(pickle.dumps(contract))
        with pytest.raises(FrozenInstanceError):
            contract.routing_key = None  # type: ignore[misc]

    @pytest.mark.parametrize("kind", [RoutingConditionKind.REACHES_ALL, RoutingConditionKind.REACHES_ANY])
    def test_reachability_selectors_require_at_least_one_queue(self, kind: RoutingConditionKind) -> None:
        with pytest.raises(TopologyValidationError, match="at least one queue"):
            RoutingCondition(kind, frozenset())

    def test_input_types_and_mandatory_contract_fields(self) -> None:
        origin = _exchange("v", "entry").id
        queue = _queue("v", "billing").id
        conditions = (RoutingCondition.exactly(()),)

        with pytest.raises(TopologyValidationError, match="nonempty tuple"):
            RoutingContract("invoice", origin, "", ())
        with pytest.raises(TopologyValidationError, match="nonempty string"):
            RoutingContract("", origin, "", conditions)
        with pytest.raises(TopologyValidationError, match="exchange NodeId"):
            RoutingContract("invoice", queue, "", conditions)
        with pytest.raises(TopologyValidationError, match="str or None"):
            RoutingContract("invoice", origin, 1, conditions)  # type: ignore[arg-type]
        with pytest.raises(TopologyValidationError, match="queue NodeId"):
            RoutingCondition.avoids({origin})
        with pytest.raises(TopologyValidationError, match="must be RoutingConditionKind"):
            RoutingCondition("exactly", frozenset({queue}))  # type: ignore[arg-type]


class TestExchangeRoutingCoverage:
    def test_result_is_frozen_canonical_and_pickleable(self) -> None:
        exchange = _exchange("v", "entry")
        first, last = _queue("v", "a").id, _queue("v", "z").id
        issue = RoutingIssue(exchange.id, RoutingUncertainty.MESSAGE_HEADERS_NOT_CAPTURED)
        result = ExchangeRoutingCoverage(exchange, RoutingStatus.DESTINATION_POSSIBLE, "", (last, first), (issue,))
        assert result.destinations == (first, last)
        assert result.routing_key == ""
        assert pickle.loads(pickle.dumps(result)) == result
        assert hash(pickle.loads(pickle.dumps(result))) == hash(result)
        with pytest.raises(FrozenInstanceError):
            result.routing_key = None  # type: ignore[misc]

    def test_result_rejects_contradictory_evidence(self) -> None:
        exchange = _exchange("v", "entry")
        queue = _queue("v", "q")
        with pytest.raises(TopologyValidationError, match="destinations require"):
            ExchangeRoutingCoverage(exchange, RoutingStatus.DESTINATION_POSSIBLE)
        with pytest.raises(TopologyValidationError, match="destinations require"):
            ExchangeRoutingCoverage(exchange, RoutingStatus.NO_DESTINATION, destinations=(queue.id,))
        with pytest.raises(TopologyValidationError, match="undetermined requires"):
            ExchangeRoutingCoverage(exchange, RoutingStatus.UNDETERMINED)

    def test_result_rejects_mutable_or_mixed_scope_data(self) -> None:
        exchange = _exchange("v", "entry")
        queue = _queue("other", "q")
        with pytest.raises(TopologyValidationError, match="share the exchange cluster and vhost"):
            ExchangeRoutingCoverage(exchange, RoutingStatus.DESTINATION_POSSIBLE, destinations=(queue.id,))
        with pytest.raises(TopologyValidationError, match="tuple of queue NodeId"):
            ExchangeRoutingCoverage(exchange, RoutingStatus.DESTINATION_POSSIBLE, destinations=[])  # type: ignore[arg-type]
        with pytest.raises(TopologyValidationError, match=r"RoutingIssue\.resource"):
            RoutingIssue("v", RoutingUncertainty.MISSING_RESOURCE)  # type: ignore[arg-type]
        with pytest.raises(TopologyValidationError, match=r"RoutingIssue\.reason"):
            RoutingIssue(exchange.id, "missing_resource")  # type: ignore[arg-type]


class TestDeadLetterCoverage:
    def test_result_is_frozen_canonical_and_pickleable(self) -> None:
        queue = _queue("v", "work")
        exchange = NodeId(vhost="v", name="dlx", kind=NodeKind.EXCHANGE)
        first, last = _queue("v", "a").id, _queue("v", "z").id
        issue = DeadLetterRoutingIssue(exchange, DeadLetterUncertainty.MESSAGE_HEADERS_NOT_CAPTURED)
        result = DeadLetterCoverage(
            queue, DeadLetterCoverageStatus.DESTINATION_POSSIBLE, exchange, "", (last, first), (issue,)
        )

        assert result.destinations == (first, last)
        assert result.routing_key == ""
        assert pickle.loads(pickle.dumps(result)) == result
        assert hash(result) == hash(pickle.loads(pickle.dumps(result)))
        with pytest.raises(FrozenInstanceError):
            result.routing_key = None  # type: ignore[misc]

    def test_result_rejects_contradictory_status_and_destinations(self) -> None:
        queue = _queue("v", "work")
        exchange = NodeId(vhost="v", name="dlx", kind=NodeKind.EXCHANGE)
        with pytest.raises(TopologyValidationError, match="destinations require"):
            DeadLetterCoverage(queue, DeadLetterCoverageStatus.DESTINATION_POSSIBLE, exchange)
        with pytest.raises(TopologyValidationError, match="destinations require"):
            DeadLetterCoverage(queue, DeadLetterCoverageStatus.NO_DESTINATION, exchange, destinations=(queue.id,))
        with pytest.raises(TopologyValidationError, match="undetermined requires"):
            DeadLetterCoverage(queue, DeadLetterCoverageStatus.UNDETERMINED, exchange)
        with pytest.raises(TopologyValidationError, match="configured exchange"):
            DeadLetterCoverage(queue, DeadLetterCoverageStatus.MISSING_EXCHANGE)
        with pytest.raises(TopologyValidationError, match="no_dlx cannot"):
            DeadLetterCoverage(queue, DeadLetterCoverageStatus.NO_DLX, exchange)

    def test_result_rejects_mixed_scope_and_mutable_collections(self) -> None:
        queue = _queue("v", "work")
        exchange = NodeId(vhost="v", name="dlx", kind=NodeKind.EXCHANGE)
        with pytest.raises(TopologyValidationError, match="share the queue cluster and vhost"):
            DeadLetterCoverage(queue, DeadLetterCoverageStatus.MISSING_EXCHANGE, replace(exchange, vhost="other"))
        with pytest.raises(TopologyValidationError, match="tuple of queue NodeId"):
            DeadLetterCoverage(queue, DeadLetterCoverageStatus.DESTINATION_POSSIBLE, exchange, destinations=[queue.id])  # type: ignore[arg-type]
        with pytest.raises(TopologyValidationError, match="reserved for streams"):
            DeadLetterCoverage(queue, DeadLetterCoverageStatus.NOT_APPLICABLE)


class TestCompetingShovelConsumers:
    def test_group_is_immutable_canonical_and_pickleable(self) -> None:
        source = _endpoint("source", is_confirmed_local=True)
        shovels = tuple(
            ShovelNode(
                id=NodeId(vhost="control", name=name, kind=NodeKind.SHOVEL),
                source=source,
                destination=_endpoint("destination"),
            )
            for name in ("z", "a")
        )
        queue = NodeId(vhost="source", name="queue", kind=NodeKind.QUEUE)

        group = CompetingShovelConsumers(queue=queue, shovels=shovels)

        assert group.shovels == shovels[::-1]
        assert pickle.loads(pickle.dumps(group)) == group
        assert hash(group) == hash(CompetingShovelConsumers(queue=queue, shovels=shovels[::-1]))
        with pytest.raises(FrozenInstanceError):
            setattr(group, "queue", queue)  # noqa: B010

    @pytest.mark.parametrize(
        "invalid",
        [
            "queue-kind",
            "queue-type",
            "empty",
            "single",
            "mutable",
            "member",
            "duplicate",
            "source",
            "unconfirmed",
            "cluster",
        ],
    )
    def test_rejects_inconsistent_or_mutable_groups(self, invalid: str) -> None:
        source = _endpoint("source", is_confirmed_local=True)
        first = ShovelNode(
            id=NodeId(vhost="control", name="first", kind=NodeKind.SHOVEL),
            source=source,
            destination=_endpoint("destination"),
        )
        second = replace(first, id=replace(first.id, name="second"))
        queue = NodeId(vhost="source", name="queue", kind=NodeKind.QUEUE)
        shovels: tuple[ShovelNode, ...] = (first, second)
        match invalid:
            case "queue-kind":
                queue = replace(queue, kind=NodeKind.EXCHANGE)
            case "queue-type":
                queue = "queue"  # type: ignore[assignment]
            case "empty":
                shovels = ()
            case "single":
                shovels = (first,)
            case "mutable":
                shovels = [first, second]  # type: ignore[assignment]
            case "member":
                shovels = (first, "second")  # type: ignore[assignment]
            case "duplicate":
                shovels = (first, first)
            case "source":
                shovels = (first, replace(second, source=replace(source, vhost="other")))
            case "unconfirmed":
                shovels = (first, replace(second, source=replace(source, is_confirmed_local=False)))
            case "cluster":
                shovels = (first, replace(second, id=replace(second.id, cluster_id="other")))

        with pytest.raises(TopologyValidationError, match="CompetingShovelConsumers"):
            CompetingShovelConsumers(queue=queue, shovels=shovels)


# ---------------------------------------------------------------------------
# NodeId
# ---------------------------------------------------------------------------


class TestNodeId:
    def test_str_shows_kind_vhost_name(self) -> None:
        assert str(NodeId(vhost="v", name="ex1", kind=NodeKind.EXCHANGE)) == "exchange:[v]/ex1"

    def test_str_shows_empty_vhost_explicitly(self) -> None:
        """ "" is a real, valid vhost name distinct from the default
        vhost "/" -- str() should make it visible, not collapse it."""
        assert str(NodeId(vhost="", name="q1", kind=NodeKind.QUEUE)) == "queue:[]/q1"

    def test_kind_is_part_of_identity(self) -> None:
        """Same vhost/name, different kind must NOT be equal -- RabbitMQ
        keeps queues and exchanges in separate namespaces."""
        exchange_id = NodeId(vhost="v", name="shared-name", kind=NodeKind.EXCHANGE)
        queue_id = NodeId(vhost="v", name="shared-name", kind=NodeKind.QUEUE)
        assert exchange_id != queue_id
        assert len({exchange_id, queue_id}) == 2

    def test_hashable_and_usable_as_dict_key(self) -> None:
        nid = NodeId(vhost="v", name="q1", kind=NodeKind.QUEUE)
        lookup = {nid: "value"}
        assert lookup[NodeId(vhost="v", name="q1", kind=NodeKind.QUEUE)] == "value"

    def test_cluster_id_is_part_of_identity(self) -> None:
        """Same RabbitMQ resource coordinates in different clusters differ."""
        primary = NodeId(cluster_id="primary-id", vhost="v", name="orders", kind=NodeKind.QUEUE)
        replica = NodeId(cluster_id="replica-id", vhost="v", name="orders", kind=NodeKind.QUEUE)

        assert primary != replica
        assert len({primary, replica}) == 2
        assert str(primary) == "queue:primary-id:[v]/orders"

    @pytest.mark.parametrize(
        ("field", "value"),
        [("vhost", 1), ("name", 1), ("kind", "queue"), ("cluster_id", 1)],
    )
    def test_rejects_invalid_public_fields(self, field: str, value: object) -> None:
        fields: dict[str, object] = {"vhost": "v", "name": "q", "kind": NodeKind.QUEUE, "cluster_id": None}
        fields[field] = value

        with pytest.raises(TopologyValidationError, match=f"NodeId.{field}"):
            NodeId(**fields)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# ExchangeNode / QueueNode / ShovelNode -- id.kind consistency
# ---------------------------------------------------------------------------


class TestExchangeNode:
    def test_valid_construction(self) -> None:
        node = _exchange("v", "ex1", exchange_type="topic")
        assert node.exchange_type == "topic"

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("", True),
            ("events", False),
            ("amq.direct", False),
        ],
    )
    def test_classifies_default_exchange_by_its_empty_name(self, name: str, expected: bool) -> None:
        assert _exchange("v", name).is_default is expected

    def test_rejects_mismatched_kind(self) -> None:
        with pytest.raises(TopologyValidationError, match="EXCHANGE"):
            ExchangeNode(
                id=NodeId(vhost="v", name="x", kind=NodeKind.QUEUE),
                exchange_type="direct",
                internal=False,
                durable=True,
            )

    def test_rejects_non_node_identity(self) -> None:
        with pytest.raises(TopologyValidationError, match=r"ExchangeNode\.id"):
            ExchangeNode(id="not-a-node", exchange_type="direct", internal=False, durable=True)  # type: ignore[arg-type]


class TestQueueLimitsConfiguration:
    def test_quorum_node_rejects_conflicting_overflow_evidence(self) -> None:
        known = ConfigurationValue("reject-publish", ConfigurationStatus.KNOWN, (), ())
        queue = _queue("v", "work", "quorum")
        with pytest.raises(TopologyValidationError, match="must match quorum delivery evidence"):
            replace(queue, queue_limits=QueueLimitsConfiguration(overflow=known))
        delivery = QuorumDeliveryConfiguration(overflow=known)
        result = replace(queue, quorum_delivery=delivery, queue_limits=QueueLimitsConfiguration(overflow=known))
        assert result == pickle.loads(pickle.dumps(result))

    def test_numeric_setting_rejects_string_values(self) -> None:
        invalid = ConfigurationValue("0", ConfigurationStatus.KNOWN, (), ())
        with pytest.raises(TopologyValidationError, match="max_length has the wrong scalar type"):
            QueueLimitsConfiguration(max_length=invalid)  # type: ignore[arg-type]


class TestQueueNode:
    def test_quorum_configuration_survives_graph_value_transfer(self) -> None:
        first = ConfigurationEvidence(ConfigurationSource.QUEUE_ARGUMENT, "x-consumer-timeout", 1000)
        second = ConfigurationEvidence(ConfigurationSource.REGULAR_POLICY, "consumer-timeout", 2000, "timeouts")
        timeout = ConfigurationValue(1000, ConfigurationStatus.KNOWN, (second, first, first), ())
        config = QuorumDeliveryConfiguration(consumer_timeout=timeout)
        node = replace(_queue("v", "q", "quorum"), quorum_delivery=config)
        assert timeout.evidence == (first, second)
        assert pickle.loads(pickle.dumps(node)) == node
        assert hash(pickle.loads(pickle.dumps(node))) == hash(node)
        assert node.id == replace(node, quorum_delivery=None).id
        with pytest.raises(FrozenInstanceError):
            config.consumer_timeout = ConfigurationValue()  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            timeout.value = 2000  # type: ignore[misc]

    @pytest.mark.parametrize("queue_type", ["classic", "stream", "plugin"])
    def test_quorum_configuration_requires_a_quorum_queue(self, queue_type: str) -> None:
        with pytest.raises(TopologyValidationError, match="requires a quorum"):
            replace(_queue("v", "q", queue_type), quorum_delivery=QuorumDeliveryConfiguration())

    def test_quorum_replication_factor_is_optional_and_pickleable(self) -> None:
        node = replace(_queue("v", "q", "quorum"), replication_factor=3)
        assert pickle.loads(pickle.dumps(node)) == node
        assert _queue("v", "q", "quorum").replication_factor is None

    def test_quorum_placement_is_frozen_and_supplies_the_factor(self) -> None:
        placement = QuorumPlacement(("rabbit@b", "rabbit@a"), "rabbit@b", ("rabbit@a",))
        node = replace(_queue("v", "q", "quorum"), quorum_placement=placement)
        assert placement.members == ("rabbit@a", "rabbit@b")
        assert node.replication_factor == 2
        assert node.id == _queue("v", "q", "quorum").id
        assert pickle.loads(pickle.dumps(node)) == node
        assert hash(pickle.loads(pickle.dumps(node))) == hash(node)
        with pytest.raises(FrozenInstanceError):
            placement.leader = "rabbit@a"  # type: ignore[misc]

    def test_quorum_placement_and_replication_factor_must_agree(self) -> None:
        quorum = _queue("v", "q", "quorum")
        with pytest.raises(TopologyValidationError, match="must match captured membership"):
            replace(quorum, replication_factor=3, quorum_placement=QuorumPlacement(("rabbit@a",)))
        with pytest.raises(TopologyValidationError, match="cannot be below reported membership"):
            replace(
                quorum,
                replication_factor=1,
                quorum_placement=QuorumPlacement(leader="rabbit@a", online_members=("rabbit@b",)),
            )
        with pytest.raises(TopologyValidationError, match="requires a quorum queue"):
            replace(_queue("v", "q", "stream"), quorum_placement=QuorumPlacement())

    @pytest.mark.parametrize(
        "evidence",
        [
            {"members": ["a"]},
            {"members": ("a", "a")},
            {"members": ("",)},
            {"leader": ""},
            {"members": ("a",), "leader": "b"},
            {"members": ("a",), "online_members": ("b",)},
        ],
    )
    def test_invalid_placement(self, evidence: dict[str, object]) -> None:
        with pytest.raises(TopologyValidationError, match="QuorumPlacement"):
            QuorumPlacement(**evidence)  # type: ignore[arg-type]

    @pytest.mark.parametrize("factor", [0, -1, True, 3.0, "3"])
    def test_invalid_replication_factor_is_rejected(self, factor: object) -> None:
        with pytest.raises(TopologyValidationError, match="replication_factor"):
            replace(_queue("v", "q", "quorum"), replication_factor=factor)  # type: ignore[arg-type]

    @pytest.mark.parametrize("queue_type", ["classic", "stream", "plugin"])
    def test_replication_factor_requires_quorum(self, queue_type: str) -> None:
        with pytest.raises(TopologyValidationError, match="requires a quorum"):
            replace(_queue("v", "q", queue_type), replication_factor=3)

    def test_valid_construction(self) -> None:
        node = _queue("v", "q1", queue_type="quorum")
        assert node.queue_type == "quorum"

    def test_rejects_mismatched_kind(self) -> None:
        with pytest.raises(TopologyValidationError, match="QUEUE"):
            QueueNode(id=NodeId(vhost="v", name="x", kind=NodeKind.EXCHANGE), queue_type="classic", durable=True)


class TestQuorumConfigurationValues:
    @pytest.mark.parametrize(
        "values",
        [
            {"value": 1},
            {"status": ConfigurationStatus.KNOWN, "uncertainties": ()},
            {"value": True, "status": ConfigurationStatus.KNOWN, "uncertainties": ()},
            {"status": ConfigurationStatus.UNKNOWN, "uncertainties": ()},
            {"status": ConfigurationStatus.UNSET},
            {"evidence": []},
            {"uncertainties": [ConfigurationUncertainty.CAPTURED_NULL]},
        ],
    )
    def test_rejects_inconsistent_setting_states(self, values: dict[str, object]) -> None:
        with pytest.raises(TopologyValidationError):
            ConfigurationValue(**values)  # type: ignore[arg-type]

    @pytest.mark.parametrize("value", [True, 1.5, [], {}])
    def test_evidence_requires_immutable_wire_scalars(self, value: object) -> None:
        with pytest.raises(TopologyValidationError):
            ConfigurationEvidence(ConfigurationSource.QUEUE_ARGUMENT, "x-consumer-timeout", value)  # type: ignore[arg-type]

    def test_null_and_unknown_are_distinct_from_absence_and_inapplicability(self) -> None:
        evidence = ConfigurationEvidence(ConfigurationSource.QUEUE_ARGUMENT, "x-consumer-timeout", None)
        unknown: ConfigurationValue[int] = ConfigurationValue(
            evidence=(evidence,), uncertainties=(ConfigurationUncertainty.CAPTURED_NULL,)
        )
        assert unknown.status == ConfigurationStatus.UNKNOWN
        assert unknown != ConfigurationValue(status=ConfigurationStatus.UNSET, uncertainties=())
        assert unknown != ConfigurationValue(status=ConfigurationStatus.NOT_APPLICABLE, uncertainties=())

    @pytest.mark.parametrize(
        "field, value",
        [
            ("consumer_timeout", -1),
            ("delayed_retry_min", 0),
            ("delivery_limit", "20"),
            ("delayed_retry_type", "exponential"),
            ("overflow", "reject-publish-dlx"),
        ],
    )
    def test_configuration_rejects_invalid_resolved_values(self, field: str, value: str | int) -> None:
        setting = ConfigurationValue(value, ConfigurationStatus.KNOWN, (), ())
        with pytest.raises(TopologyValidationError):
            QuorumDeliveryConfiguration(**{field: setting})  # type: ignore[arg-type]

    def test_audit_result_is_canonical_and_pickleable(self) -> None:
        queue = _queue("v", "q", "quorum")
        issues = (
            QuorumConfigurationIssue.RETRY_DISABLED_WITHOUT_MINIMUM,
            QuorumConfigurationIssue.FINITE_DELIVERY_LIMIT_WITHOUT_DLX,
        )
        result = QuorumQueueConfiguration(queue.id, QuorumDeliveryConfiguration(), (*issues, issues[0]))
        assert result.issues == tuple(sorted(issues))
        assert pickle.loads(pickle.dumps(result)) == result
        with pytest.raises(TopologyValidationError):
            QuorumQueueConfiguration(_exchange("v", "e").id, result.configuration)


class TestFanoutValues:
    def test_results_are_canonical_immutable_and_pickleable(self) -> None:
        exchange = _exchange("v", "events", "fanout")
        queues = tuple(
            FanoutQueue(id=_queue("v", name).id, queue_type="quorum", replication_factor=3, durable=True)
            for name in ("z", "a")
        )
        result = FanoutExchangeBindings(exchange=exchange, queues=queues)
        assert result.queues == queues[::-1]
        assert result.queue_count == result.durable_queue_count == result.quorum_queue_count == 2
        assert result.known_quorum_member_count == result.quorum_member_count == 6
        assert pickle.loads(pickle.dumps(result)) == result
        assert hash(result) == hash(FanoutExchangeBindings(exchange=exchange, queues=queues[::-1]))
        with pytest.raises(FrozenInstanceError):
            setattr(result, "queues", ())  # noqa: B010
        with pytest.raises(FrozenInstanceError):
            setattr(queues[0], "replication_factor", 3)  # noqa: B010

    @pytest.mark.parametrize("invalid", ["exchange", "mutable", "target", "duplicate", "vhost", "cluster"])
    def test_invalid_exchange_groups_are_rejected(self, invalid: str) -> None:
        exchange = _exchange("v", "events", "fanout")
        queue = FanoutQueue(id=_queue("v", "q").id)
        queues = (queue,)
        match invalid:
            case "exchange":
                exchange = replace(exchange, exchange_type="direct")
            case "mutable":
                queues = [queue]  # type: ignore[assignment]
            case "target":
                queues = ("queue",)  # type: ignore[assignment]
            case "duplicate":
                queues = (queue, queue)  # type: ignore[assignment]
            case "vhost":
                queues = (replace(queue, id=replace(queue.id, vhost="other")),)
            case "cluster":
                queues = (replace(queue, id=replace(queue.id, cluster_id="other")),)
        with pytest.raises(TopologyValidationError, match="FanoutExchangeBindings"):
            FanoutExchangeBindings(exchange=exchange, queues=queues)

    @pytest.mark.parametrize("factor", [0, -1, True, 2.5, "3"])
    def test_invalid_target_replication_is_rejected(self, factor: object) -> None:
        with pytest.raises(TopologyValidationError, match="replication_factor"):
            FanoutQueue(id=_queue("v", "q").id, queue_type="quorum", replication_factor=factor)  # type: ignore[arg-type]

    @pytest.mark.parametrize("durable", [0, 1, "true"])
    def test_durability_requires_a_boolean_or_unknown(self, durable: object) -> None:
        with pytest.raises(TopologyValidationError, match="durable must be bool or None"):
            FanoutQueue(id=_queue("v", "q").id, durable=durable)  # type: ignore[arg-type]

    def test_target_identity_type_and_replication_must_be_consistent(self) -> None:
        with pytest.raises(TopologyValidationError, match="queue NodeId"):
            FanoutQueue(id=_exchange("v", "e").id)
        with pytest.raises(TopologyValidationError, match="queue_type"):
            FanoutQueue(id=_queue("v", "q").id, queue_type=3)  # type: ignore[arg-type]
        with pytest.raises(TopologyValidationError, match="requires a quorum"):
            FanoutQueue(id=_queue("v", "q").id, queue_type=None, replication_factor=3)


class TestEndpointAuthority:
    def test_canonicalizes_scheme_and_host_casing(self) -> None:
        authority = EndpointAuthority(scheme="AMQPS", host="Rabbit.EXAMPLE", port=5671)

        assert authority.scheme == "amqps"
        assert authority.host == "rabbit.example"
        assert authority == EndpointAuthority(scheme="amqps", host="rabbit.example", port=5671)
        assert hash(authority) == hash(EndpointAuthority(scheme="amqps", host="rabbit.example", port=5671))

    @pytest.mark.parametrize("port", [0, 65536, True, "5672"])
    def test_rejects_invalid_port(self, port: object) -> None:
        with pytest.raises(TopologyValidationError, match="port"):
            EndpointAuthority(scheme="amqp", host="rabbit.example", port=port)  # type: ignore[arg-type]


class TestResourceEndpoint:
    def test_retains_all_failover_authorities(self) -> None:
        endpoint = ResourceEndpoint(
            protocol="amqp091",
            authorities=(
                EndpointAuthority(scheme="amqp", host="rabbit-1", port=5672),
                EndpointAuthority(scheme="amqp", host="rabbit-2", port=5672),
            ),
            vhost="orders",
            resource_name="orders.q",
            resource_kind=NodeKind.QUEUE,
            routing_key=None,
            is_confirmed_local=False,
        )

        assert tuple(authority.host for authority in endpoint.authorities or ()) == ("rabbit-1", "rabbit-2")

    def test_rejects_partial_resource_identity(self) -> None:
        with pytest.raises(TopologyValidationError, match="must either both be present"):
            ResourceEndpoint(
                protocol="amqp091",
                authorities=None,
                vhost="orders",
                resource_name="orders.q",
                resource_kind=None,
                routing_key=None,
            )

    def test_rejects_non_boolean_locality(self) -> None:
        with pytest.raises(TopologyValidationError, match="is_confirmed_local must be bool"):
            ResourceEndpoint(
                protocol="local",
                authorities=(),
                vhost="orders",
                resource_name="orders.q",
                resource_kind=NodeKind.QUEUE,
                routing_key=None,
                is_confirmed_local=None,  # type: ignore[arg-type]
            )

    @pytest.mark.parametrize("resource_kind", [None, NodeKind.QUEUE])
    def test_routing_key_requires_exchange_resource(self, resource_kind: NodeKind | None) -> None:
        with pytest.raises(TopologyValidationError, match="only valid for exchange endpoints"):
            ResourceEndpoint(
                protocol="amqp091",
                authorities=None,
                vhost="orders",
                resource_name=None if resource_kind is None else "orders.q",
                resource_kind=resource_kind,
                routing_key="orders.created",
            )

    def test_exchange_resource_accepts_routing_key(self) -> None:
        endpoint = ResourceEndpoint(
            protocol="amqp091",
            authorities=None,
            vhost="orders",
            resource_name="orders.events",
            resource_kind=NodeKind.EXCHANGE,
            routing_key="orders.created",
        )

        assert endpoint.routing_key == "orders.created"

    @pytest.mark.parametrize("protocol", [None, "amqp091", "amqp10", "unknown"])
    def test_empty_authorities_are_reserved_for_local_protocol(self, protocol: str | None) -> None:
        with pytest.raises(TopologyValidationError, match="empty only when protocol is 'local'"):
            ResourceEndpoint(
                protocol=protocol,
                authorities=(),
                vhost="orders",
                resource_name="orders.q",
                resource_kind=NodeKind.QUEUE,
                routing_key=None,
            )

    def test_confirmed_local_requires_known_vhost(self) -> None:
        with pytest.raises(TopologyValidationError, match=r"requires a known ResourceEndpoint\.vhost"):
            ResourceEndpoint(
                protocol="local",
                authorities=(),
                vhost=None,
                resource_name="orders.q",
                resource_kind=NodeKind.QUEUE,
                routing_key=None,
                is_confirmed_local=True,
            )

    def test_confirmed_local_requires_normalized_authorities(self) -> None:
        with pytest.raises(TopologyValidationError, match=r"requires normalized ResourceEndpoint\.authorities"):
            ResourceEndpoint(
                protocol="amqp091",
                authorities=None,
                vhost="orders",
                resource_name="orders.q",
                resource_kind=NodeKind.QUEUE,
                routing_key=None,
                is_confirmed_local=True,
            )

    def test_confirmed_local_accepts_complete_local_evidence(self) -> None:
        endpoint = ResourceEndpoint(
            protocol="local",
            authorities=(),
            vhost="",
            resource_name="orders.q",
            resource_kind=NodeKind.QUEUE,
            routing_key=None,
            is_confirmed_local=True,
        )

        assert endpoint.is_confirmed_local is True


class TestShovelNode:
    def test_rejects_mismatched_kind(self) -> None:
        with pytest.raises(TopologyValidationError, match="SHOVEL"):
            ShovelNode(
                id=NodeId(vhost="v", name="x", kind=NodeKind.QUEUE),
                source=_endpoint("v"),
                destination=_endpoint("v"),
            )

    def test_is_cross_vhost_true_when_vhosts_differ(self) -> None:
        node = ShovelNode(
            id=NodeId(vhost="a", name="s", kind=NodeKind.SHOVEL),
            source=_endpoint("a"),
            destination=_endpoint("b"),
        )
        assert node.is_cross_vhost is True

    def test_is_cross_vhost_false_when_vhosts_match(self) -> None:
        node = ShovelNode(
            id=NodeId(vhost="a", name="s", kind=NodeKind.SHOVEL),
            source=_endpoint("a"),
            destination=_endpoint("a"),
        )
        assert node.is_cross_vhost is False

    @pytest.mark.parametrize(("src", "dest"), [(None, "a"), ("a", None), (None, None)])
    def test_is_cross_vhost_none_when_either_side_unresolved(self, src: str | None, dest: str | None) -> None:
        """None means "unknown," not "same vhost" -- guessing here
        would risk hiding a real cross-vhost link from cluster-split
        analysis."""
        node = ShovelNode(
            id=NodeId(vhost="a", name="s", kind=NodeKind.SHOVEL),
            source=_endpoint(src),
            destination=_endpoint(dest),
        )
        assert node.is_cross_vhost is None

    def test_has_unconfirmed_endpoint_when_locality_is_not_confirmed(self) -> None:
        node = ShovelNode(
            id=NodeId(vhost="a", name="s", kind=NodeKind.SHOVEL),
            source=_endpoint("a", is_confirmed_local=True),
            destination=_endpoint("b", is_confirmed_local=False),
        )
        assert node.has_unconfirmed_endpoint is True

    def test_endpoint_is_unconfirmed_by_default(self) -> None:
        node = ShovelNode(
            id=NodeId(vhost="a", name="s", kind=NodeKind.SHOVEL),
            source=_endpoint("a"),
            destination=_endpoint("a"),
        )
        assert node.has_unconfirmed_endpoint is True

    def test_has_no_unconfirmed_endpoint_when_both_sides_are_confirmed(self) -> None:
        node = ShovelNode(
            id=NodeId(vhost="a", name="s", kind=NodeKind.SHOVEL),
            source=_endpoint("a", is_confirmed_local=True),
            destination=_endpoint("a", is_confirmed_local=True),
        )
        assert node.has_unconfirmed_endpoint is False


# ---------------------------------------------------------------------------
# TopologyEdge
# ---------------------------------------------------------------------------


class TestTopologyEdge:
    def test_str(self) -> None:
        edge = TopologyEdge(
            source=NodeId(vhost="v", name="a", kind=NodeKind.EXCHANGE),
            target=NodeId(vhost="v", name="b", kind=NodeKind.QUEUE),
            kind=EdgeKind.BINDING,
            routing_key="rk",
        )
        assert str(edge) == "exchange:[v]/a -> queue:[v]/b [binding]"

    def test_empty_string_routing_key_distinct_from_none(self) -> None:
        """RabbitMQ allows binding with routing_key="" -- a real,
        distinct value that must not collapse with "not applicable"."""
        source, target = (
            NodeId(vhost="v", name="a", kind=NodeKind.EXCHANGE),
            NodeId(vhost="v", name="b", kind=NodeKind.QUEUE),
        )
        with_empty = TopologyEdge(source=source, target=target, kind=EdgeKind.BINDING, routing_key="")
        without = TopologyEdge(source=source, target=target, kind=EdgeKind.BINDING, routing_key=None)
        assert with_empty != without

    def test_arguments_distinguishes_otherwise_identical_bindings(self) -> None:
        """The headers-exchange case: same (often blank) routing_key,
        different match criteria -- must not collapse to one edge."""
        source, target = (
            NodeId(vhost="v", name="headers.ex", kind=NodeKind.EXCHANGE),
            NodeId(vhost="v", name="q1", kind=NodeKind.QUEUE),
        )
        edge_a = TopologyEdge(
            source=source, target=target, kind=EdgeKind.BINDING, routing_key="", arguments='{"format": "pdf"}'
        )
        edge_b = TopologyEdge(
            source=source, target=target, kind=EdgeKind.BINDING, routing_key="", arguments='{"format": "docx"}'
        )
        assert edge_a != edge_b
        assert len({edge_a, edge_b}) == 2

    def test_hashable(self) -> None:
        edge = TopologyEdge(
            source=NodeId(vhost="v", name="a", kind=NodeKind.EXCHANGE),
            target=NodeId(vhost="v", name="b", kind=NodeKind.QUEUE),
            kind=EdgeKind.BINDING,
        )
        assert edge in {edge}

    def test_rejects_endpoint_kinds_that_do_not_describe_a_binding(self) -> None:
        with pytest.raises(TopologyValidationError, match="invalid endpoint kinds"):
            TopologyEdge(
                source=NodeId(vhost="v", name="q", kind=NodeKind.QUEUE),
                target=NodeId(vhost="v", name="ex", kind=NodeKind.EXCHANGE),
                kind=EdgeKind.BINDING,
            )

    def test_rejects_binding_arguments_on_non_binding_edges(self) -> None:
        with pytest.raises(TopologyValidationError, match="arguments is only valid"):
            TopologyEdge(
                source=NodeId(vhost="v", name="q", kind=NodeKind.QUEUE),
                target=NodeId(vhost="v", name="dlx", kind=NodeKind.EXCHANGE),
                kind=EdgeKind.DEAD_LETTER,
                arguments="{}",
            )

    def test_rejects_routing_key_on_alternate_exchange_edge(self) -> None:
        with pytest.raises(TopologyValidationError, match="routing_key must be None"):
            TopologyEdge(
                source=NodeId(vhost="v", name="source", kind=NodeKind.EXCHANGE),
                target=NodeId(vhost="v", name="alternate", kind=NodeKind.EXCHANGE),
                kind=EdgeKind.ALTERNATE_EXCHANGE,
                routing_key="not-applicable",
            )

    @pytest.mark.parametrize(
        ("kind", "source_kind", "target_kind"),
        [
            (EdgeKind.BINDING, NodeKind.EXCHANGE, NodeKind.QUEUE),
            (EdgeKind.DEAD_LETTER, NodeKind.QUEUE, NodeKind.EXCHANGE),
            (EdgeKind.ALTERNATE_EXCHANGE, NodeKind.EXCHANGE, NodeKind.EXCHANGE),
        ],
    )
    def test_non_shovel_edges_cannot_cross_vhosts(
        self,
        kind: EdgeKind,
        source_kind: NodeKind,
        target_kind: NodeKind,
    ) -> None:
        with pytest.raises(TopologyValidationError, match="must stay within one vhost"):
            TopologyEdge(
                source=NodeId(vhost="source-vhost", name="source", kind=source_kind),
                target=NodeId(vhost="target-vhost", name="target", kind=target_kind),
                kind=kind,
            )

    @pytest.mark.parametrize(
        ("kind", "source_kind", "target_kind"),
        [
            (EdgeKind.BINDING, NodeKind.EXCHANGE, NodeKind.QUEUE),
            (EdgeKind.DEAD_LETTER, NodeKind.QUEUE, NodeKind.EXCHANGE),
            (EdgeKind.ALTERNATE_EXCHANGE, NodeKind.EXCHANGE, NodeKind.EXCHANGE),
        ],
    )
    def test_non_shovel_edges_cannot_cross_clusters(
        self,
        kind: EdgeKind,
        source_kind: NodeKind,
        target_kind: NodeKind,
    ) -> None:
        with pytest.raises(TopologyValidationError, match="must stay within one cluster"):
            TopologyEdge(
                source=NodeId(cluster_id="source-cluster", vhost="v", name="source", kind=source_kind),
                target=NodeId(cluster_id="target-cluster", vhost="v", name="target", kind=target_kind),
                kind=kind,
            )

    def test_shovel_edge_can_cross_cluster_and_vhost_boundaries(self) -> None:
        edge = TopologyEdge(
            source=NodeId(cluster_id="source-cluster", vhost="source-vhost", name="orders.q", kind=NodeKind.QUEUE),
            target=NodeId(
                cluster_id="target-cluster",
                vhost="target-vhost",
                name="orders-shovel",
                kind=NodeKind.SHOVEL,
            ),
            kind=EdgeKind.SHOVEL,
        )

        assert edge.source.cluster_id != edge.target.cluster_id
        assert edge.source.vhost != edge.target.vhost

    @pytest.mark.parametrize(
        ("source_kind", "target_kind"),
        [
            (NodeKind.QUEUE, NodeKind.SHOVEL),
            (NodeKind.SHOVEL, NodeKind.QUEUE),
        ],
    )
    def test_shovel_edge_connected_to_queue_rejects_routing_key(
        self,
        source_kind: NodeKind,
        target_kind: NodeKind,
    ) -> None:
        with pytest.raises(TopologyValidationError, match="connected to queues"):
            TopologyEdge(
                source=NodeId(vhost="v", name="source", kind=source_kind),
                target=NodeId(vhost="v", name="target", kind=target_kind),
                kind=EdgeKind.SHOVEL,
                routing_key="not-applicable",
            )

    @pytest.mark.parametrize(
        ("source_kind", "target_kind"),
        [
            (NodeKind.EXCHANGE, NodeKind.SHOVEL),
            (NodeKind.SHOVEL, NodeKind.EXCHANGE),
        ],
    )
    def test_shovel_edge_connected_to_exchange_accepts_routing_key(
        self,
        source_kind: NodeKind,
        target_kind: NodeKind,
    ) -> None:
        edge = TopologyEdge(
            source=NodeId(vhost="v", name="source", kind=source_kind),
            target=NodeId(vhost="v", name="target", kind=target_kind),
            kind=EdgeKind.SHOVEL,
            routing_key="orders.created",
        )

        assert edge.routing_key == "orders.created"


# ---------------------------------------------------------------------------
# ClusterTopology
# ---------------------------------------------------------------------------


class TestQueueInventoryValues:
    def test_values_are_frozen_canonical_and_pickleable(self) -> None:
        first, last = _queue("a", "a"), _queue("a", "z")
        group = QueueTypeGroup("classic", (last, first))
        populated = VhostQueueInventory("a", "quorum", (QueueTypeGroup("stream", ()), group))
        empty = VhostQueueInventory("z", None, ())
        inventory = QueueTypeInventory((empty, populated))

        assert group.queues == (first, last)
        assert populated.queue_types == (group, QueueTypeGroup("stream", ()))
        assert inventory.vhosts == (populated, empty)
        assert inventory.queue_count == 2
        assert pickle.loads(pickle.dumps(inventory)) == inventory
        assert hash(pickle.loads(pickle.dumps(inventory))) == hash(inventory)
        with pytest.raises(FrozenInstanceError):
            inventory.vhosts = ()  # type: ignore[misc]
        metadata = VirtualHostMetadata("a", "quorum")
        with pytest.raises(FrozenInstanceError):
            metadata.default_queue_type = "classic"  # type: ignore[misc]

    @pytest.mark.parametrize("queue_type", ["classic", "plugin-type"])
    def test_type_group_rejects_mismatched_types_and_duplicate_identities(self, queue_type: str) -> None:
        queue = _queue("v", "q", queue_type=queue_type)
        with pytest.raises(TopologyValidationError, match="match the group queue type"):
            QueueTypeGroup("quorum", (queue,))
        with pytest.raises(TopologyValidationError, match="distinct identities"):
            QueueTypeGroup(queue_type, (queue, queue))

    def test_vhost_inventory_rejects_conflicting_queue_identities(self) -> None:
        classic = _queue("v", "q")
        quorum = replace(classic, queue_type="quorum")
        with pytest.raises(TopologyValidationError, match="distinct identities"):
            VhostQueueInventory("v", None, (QueueTypeGroup("classic", (classic,)), QueueTypeGroup("quorum", (quorum,))))

    def test_vhost_inventory_rejects_duplicate_types_and_wrong_vhost(self) -> None:
        group = QueueTypeGroup("classic", (_queue("v", "q"),))
        with pytest.raises(TopologyValidationError, match="queue types must be distinct"):
            VhostQueueInventory("v", None, (group, group))
        with pytest.raises(TopologyValidationError, match="belong to its vhost"):
            VhostQueueInventory("other", None, (group,))

    def test_inventory_rejects_duplicate_vhosts_and_mixed_clusters(self) -> None:
        queue = _queue("v", "q")
        first = VhostQueueInventory("v", None, (QueueTypeGroup("classic", (queue,)),))
        with pytest.raises(TopologyValidationError, match="distinct names"):
            QueueTypeInventory((first, first))
        other_cluster = replace(queue, id=replace(queue.id, cluster_id="other", vhost="other"))
        second = VhostQueueInventory("other", None, (QueueTypeGroup("classic", (other_cluster,)),))
        with pytest.raises(TopologyValidationError, match="one cluster"):
            QueueTypeInventory((first, second))


class TestClusterTopology:
    def test_pickle_round_trip_rebuilds_derived_caches(self) -> None:
        cluster_id = "cluster-id"
        exchange = ExchangeNode(
            id=NodeId(cluster_id=cluster_id, vhost="v", name="events", kind=NodeKind.EXCHANGE),
            exchange_type="topic",
            internal=False,
            durable=True,
        )
        queue = QueueNode(
            id=NodeId(cluster_id=cluster_id, vhost="v", name="orders", kind=NodeKind.QUEUE),
            queue_type="quorum",
            durable=True,
        )
        shovel = ShovelNode(
            id=NodeId(cluster_id=cluster_id, vhost="v", name="replicate", kind=NodeKind.SHOVEL),
            source=_endpoint("v"),
            destination=_endpoint("v"),
        )
        edge = TopologyEdge(
            source=exchange.id,
            target=queue.id,
            kind=EdgeKind.BINDING,
            routing_key="orders.created",
        )
        topology = ClusterTopology(
            exchanges=frozenset({exchange}),
            queues=frozenset({queue}),
            shovels=frozenset({shovel}),
            edges=frozenset({edge}),
            cluster_id=cluster_id,
            cluster_name="rabbit@cluster",
            cluster_label="production",
            vhosts=frozenset({VirtualHostMetadata("v", "classic"), VirtualHostMetadata("empty")}),
        )

        restored = pickle.loads(pickle.dumps(topology, protocol=pickle.HIGHEST_PROTOCOL))

        assert isinstance(restored, ClusterTopology)
        assert restored == topology
        assert restored.cluster_label == topology.cluster_label
        assert restored.vhosts == topology.vhosts
        assert restored.nodes == topology.nodes
        assert restored.all_node_ids == topology.all_node_ids
        assert restored.nodes_by_id == topology.nodes_by_id
        assert restored.nodes_by_id is not topology.nodes_by_id
        with pytest.raises(TypeError):
            restored.nodes_by_id[exchange.id] = exchange  # type: ignore[index]

    def test_cluster_label_does_not_change_graph_equality_or_hash(self) -> None:
        topology = ClusterTopology(
            exchanges=frozenset(),
            queues=frozenset(),
            shovels=frozenset(),
            edges=frozenset(),
            cluster_name="broker-name",
        )

        relabeled = replace(topology, cluster_label="production-eu-west-1")

        assert relabeled == topology
        assert hash(relabeled) == hash(topology)
        assert replace(topology, cluster_name="renamed-broker") != topology

    def test_vhost_metadata_affects_topology_equality_without_adding_nodes(self) -> None:
        topology = ClusterTopology(frozenset(), frozenset(), frozenset(), frozenset())
        captured = replace(topology, vhosts=frozenset({VirtualHostMetadata("v", "quorum")}))

        assert captured != topology
        assert captured.nodes == topology.nodes
        assert captured.all_node_ids == topology.all_node_ids
        assert captured.edges == topology.edges

    def test_rejects_conflicting_vhost_metadata(self) -> None:
        with pytest.raises(TopologyValidationError, match="conflicting virtual host metadata"):
            ClusterTopology(
                frozenset(),
                frozenset(),
                frozenset(),
                frozenset(),
                vhosts=frozenset({VirtualHostMetadata("v", "classic"), VirtualHostMetadata("v", "quorum")}),
            )

    def test_rejects_mutable_vhost_metadata_collection(self) -> None:
        with pytest.raises(TopologyValidationError, match=r"ClusterTopology\.vhosts must be a frozenset"):
            ClusterTopology(frozenset(), frozenset(), frozenset(), frozenset(), vhosts=[])  # type: ignore[arg-type]

    def test_rejects_non_string_cluster_name_evidence(self) -> None:
        with pytest.raises(TopologyValidationError, match=r"ClusterTopology\.cluster_name"):
            ClusterTopology(
                exchanges=frozenset(),
                queues=frozenset(),
                shovels=frozenset(),
                edges=frozenset(),
                cluster_name=123,  # type: ignore[arg-type]
            )

    def test_nodes_and_ids_span_all_three_kinds(self) -> None:
        ex, q = _exchange("v", "ex1"), _queue("v", "q1")
        shovel = ShovelNode(
            id=NodeId(vhost="v", name="s1", kind=NodeKind.SHOVEL),
            source=_endpoint("v"),
            destination=_endpoint("v"),
        )
        topo = ClusterTopology(
            exchanges=frozenset({ex}), queues=frozenset({q}), shovels=frozenset({shovel}), edges=frozenset()
        )

        nodes: frozenset[TopologyNode] = topo.nodes
        node_ids = topo.all_node_ids
        assert nodes == frozenset({ex, q, shovel})
        assert node_ids == {ex.id, q.id, shovel.id}
        assert topo.nodes is nodes
        assert topo.all_node_ids is node_ids

    def test_nodes_can_be_counted_directly(self) -> None:
        topo = ClusterTopology(
            exchanges=frozenset({_exchange("v", "a"), _exchange("v", "b")}),
            queues=frozenset({_queue("v", "q")}),
            shovels=frozenset(),
            edges=frozenset(),
        )
        assert len(topo.nodes) == 3

    def test_nodes_by_id_lookup(self) -> None:
        ex = _exchange("v", "ex1")
        topo = ClusterTopology(exchanges=frozenset({ex}), queues=frozenset(), shovels=frozenset(), edges=frozenset())
        nodes_by_id = topo.nodes_by_id

        assert nodes_by_id[ex.id] is ex
        assert topo.nodes_by_id is nodes_by_id

        with pytest.raises(TypeError):
            topo.nodes_by_id[ex.id] = ex  # type: ignore[index]

    def test_dangling_edge_detected(self) -> None:
        q = _queue("v", "q1")
        edge = TopologyEdge(
            source=q.id,
            target=NodeId(vhost="v", name="never-created", kind=NodeKind.EXCHANGE),
            kind=EdgeKind.DEAD_LETTER,
        )
        topo = ClusterTopology(
            exchanges=frozenset(), queues=frozenset({q}), shovels=frozenset(), edges=frozenset({edge})
        )
        assert topo.dangling_edges() == frozenset({edge})

    def test_no_dangling_edges_when_all_resolved(self) -> None:
        ex, q = _exchange("v", "ex1"), _queue("v", "q1")
        edge = TopologyEdge(source=ex.id, target=q.id, kind=EdgeKind.BINDING, routing_key="rk")
        topo = ClusterTopology(
            exchanges=frozenset({ex}), queues=frozenset({q}), shovels=frozenset(), edges=frozenset({edge})
        )
        assert topo.dangling_edges() == frozenset()

    def test_rejects_conflicting_node_data_for_same_id(self) -> None:
        """Two nodes sharing an ID must not disagree on resource attributes."""
        nid = NodeId(vhost="v", name="ex1", kind=NodeKind.EXCHANGE)
        a = ExchangeNode(id=nid, exchange_type="direct", internal=False, durable=True)
        b = ExchangeNode(id=nid, exchange_type="topic", internal=False, durable=True)
        with pytest.raises(TopologyValidationError, match="conflicting node data"):
            ClusterTopology(exchanges=frozenset({a, b}), queues=frozenset(), shovels=frozenset(), edges=frozenset())

    def test_rejects_node_from_a_different_cluster(self) -> None:
        node = ExchangeNode(
            id=NodeId(cluster_id="secondary", vhost="v", name="ex1", kind=NodeKind.EXCHANGE),
            exchange_type="direct",
            internal=False,
            durable=True,
        )

        with pytest.raises(TopologyValidationError, match="belongs to cluster"):
            ClusterTopology(
                exchanges=frozenset({node}),
                queues=frozenset(),
                shovels=frozenset(),
                edges=frozenset(),
                cluster_id="primary",
            )

    def test_validation_error_is_a_public_sdk_error(self) -> None:
        with pytest.raises(RabbitMQError):
            ExchangeNode(
                id=NodeId(vhost="v", name="x", kind=NodeKind.QUEUE),
                exchange_type="direct",
                internal=False,
                durable=True,
            )

    def test_allows_exact_duplicate_node_data_for_same_id(self) -> None:
        """Two ExchangeNode objects identical in every field are a
        harmless duplicate, not a conflict -- should collapse quietly."""
        nid = NodeId(vhost="v", name="ex1", kind=NodeKind.EXCHANGE)
        a = ExchangeNode(id=nid, exchange_type="direct", internal=False, durable=True)
        b = ExchangeNode(id=nid, exchange_type="direct", internal=False, durable=True)
        topo = ClusterTopology(exchanges=frozenset({a, b}), queues=frozenset(), shovels=frozenset(), edges=frozenset())
        assert len(topo.nodes) == 1

    def test_rejects_mutable_node_collection(self) -> None:
        with pytest.raises(TopologyValidationError, match=r"ClusterTopology\.exchanges must be a frozenset"):
            ClusterTopology(
                exchanges=set(),  # type: ignore[arg-type]
                queues=frozenset(),
                shovels=frozenset(),
                edges=frozenset(),
            )
