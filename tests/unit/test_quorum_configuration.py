"""Resolve quorum audit evidence without inferring broker or consumer state."""

from __future__ import annotations

import pickle
from typing import TYPE_CHECKING

import pytest
from tests.shared.parser_fixtures import _binding, _exchange, _observed_queue, _policy, _queue, _response, _vhost

from rabbitmq_management_sdk import ClusterAuditor
from rabbitmq_management_sdk.resources.v4.admin.schemas.export_response import ClusterDefinitionsResponse
from rabbitmq_management_sdk.topology import (
    ClusterTopology,
    ConfigurationEvidence,
    ConfigurationSource,
    ConfigurationStatus,
    ConfigurationUncertainty,
    DeadLetterCoverageStatus,
    DeadLetterRoutingIssue,
    DeadLetterUncertainty,
    EdgeKind,
    NodeId,
    NodeKind,
    QueueNode,
    QuorumConfigurationIssue,
)

if TYPE_CHECKING:
    from rabbitmq_management_sdk.topology import QuorumDeliveryConfiguration

pytestmark = pytest.mark.unit


def _auditor(
    arguments: dict[str, object] | None = None,
    regular: dict[str, object] | None = None,
    operator: dict[str, object] | None = None,
    *,
    version: str | None = "4.3.8",
    observations: bool = True,
    observed_fields: dict[str, object] | None = None,
) -> ClusterAuditor:
    definition = _response(
        rabbit_version=version,
        queues=[_queue("q", "v", **{"x-queue-type": "quorum", **(arguments or {})})],
        exchanges=[_exchange("dlx", "v")],
        policies=[_policy("regular", "^q$", "quorum_queues", 1, vhost="v", definition=regular)]
        if regular is not None
        else [],
        parameters=[
            {
                "component": "operator_policy",
                "vhost": "v",
                "name": "operator",
                "value": {"pattern": "^q$", "priority": 1, "apply-to": "quorum_queues", "definition": operator},
            }
        ]
        if operator is not None
        else [],
    )
    if not observations:
        return ClusterAuditor(definition)
    fields: dict[str, object] = {
        "policy": "regular" if regular is not None else None,
        "operator_policy": "operator" if operator is not None else None,
        **(observed_fields or {}),
    }
    return ClusterAuditor(definition, queues=[_observed_queue("q", "v", **fields)], exchanges=[])


def _config(auditor: ClusterAuditor) -> QuorumDeliveryConfiguration:
    (result,) = auditor.quorum_delivery_configuration()
    queue = next(queue for queue in auditor.topology.queues if queue.id == result.queue)
    assert queue.quorum_delivery == result.configuration
    assert auditor.audit().quorum_delivery_configuration == (result,)
    return result.configuration


def test_fixed_defaults_are_distinct_from_uncaptured_global_timeouts() -> None:
    auditor = _auditor()
    config = _config(auditor)
    assert config.delayed_retry_type.value == "disabled"
    assert config.delayed_retry_min.status == ConfigurationStatus.NOT_APPLICABLE
    assert config.delivery_limit.value == 20
    assert config.delivery_count_basis.value == "failed"
    assert config.overflow.value == "drop-head"
    assert config.effective_dead_letter_strategy.value == "disabled"
    for setting in (config.consumer_timeout, config.consumer_disconnected_timeout):
        assert setting.status == ConfigurationStatus.UNKNOWN
        assert setting.uncertainties == (ConfigurationUncertainty.BROKER_DEFAULT_NOT_CAPTURED,)
    assert auditor.quorum_delivery_configuration()[0].issues == (
        QuorumConfigurationIssue.FINITE_DELIVERY_LIMIT_WITHOUT_DLX,
    )


def test_queue_observation_supplies_arguments_when_the_definitions_map_is_omitted() -> None:
    queue = _queue("q", "v")
    del queue["arguments"]
    definitions = _response(rabbit_version="4.3.8", vhosts=[_vhost("v", "quorum")], queues=[queue])
    observed = _observed_queue(
        "q",
        "v",
        policy=None,
        operator_policy=None,
        arguments={
            "x-queue-type": "quorum",
            "x-delivery-limit": -1,
            "x-delayed-retry-type": "all",
            "x-delayed-retry-min": 1000,
            "x-overflow": "reject-publish",
            "x-consumer-timeout": None,
        },
    )
    auditor = ClusterAuditor(definitions, queues=[observed], exchanges=[])
    config = _config(auditor)
    assert config.delivery_limit.value == -1
    assert config.delivery_limit.evidence == (
        ConfigurationEvidence(ConfigurationSource.OBSERVED_QUEUE_ARGUMENT, "x-delivery-limit", -1),
    )
    assert config.delayed_retry_type.value == "all"
    assert config.delayed_retry_max.value == config.delayed_retry_min.value == 1000
    assert config.overflow.value == "reject-publish"
    assert config.consumer_timeout.uncertainties == (ConfigurationUncertainty.CAPTURED_NULL,)
    assert config.consumer_timeout.evidence == (
        ConfigurationEvidence(ConfigurationSource.OBSERVED_QUEUE_ARGUMENT, "x-consumer-timeout", None),
    )
    assert not auditor.quorum_delivery_configuration()[0].issues


def test_missing_quorum_argument_map_cannot_establish_defaults() -> None:
    queue = _queue("q", "v")
    del queue["arguments"]
    auditor = ClusterAuditor(_response(rabbit_version="4.3.8", vhosts=[_vhost("v", "quorum")], queues=[queue]))
    config = _config(auditor)
    for setting in (config.delivery_limit, config.delayed_retry_type, config.effective_dead_letter_strategy):
        assert setting.status == ConfigurationStatus.UNKNOWN
        assert setting.uncertainties == (ConfigurationUncertainty.ARGUMENTS_NOT_CAPTURED,)
    assert not auditor.quorum_delivery_configuration()[0].issues
    (coverage,) = auditor.dead_letter_coverage()
    assert coverage.status == DeadLetterCoverageStatus.UNDETERMINED
    assert coverage.dead_letter_exchange is None
    assert coverage.issues == (DeadLetterRoutingIssue(coverage.queue.id, DeadLetterUncertainty.DLX_ROUTE_NOT_CAPTURED),)


@pytest.mark.parametrize(
    ("source", "exchange_name"),
    [
        (ConfigurationSource.OBSERVED_QUEUE_ARGUMENT, "dlx"),
        (ConfigurationSource.OBSERVED_QUEUE_ARGUMENT, ""),
        (ConfigurationSource.EFFECTIVE_POLICY, "dlx"),
    ],
)
def test_quorum_dlx_coverage_preserves_captured_evidence(source: ConfigurationSource, exchange_name: str) -> None:
    queue = _queue("q", "v", **{"x-queue-type": "quorum"})
    policies = []
    if source == ConfigurationSource.OBSERVED_QUEUE_ARGUMENT:
        del queue["arguments"]
        key = "x-dead-letter-exchange"
        arguments = {"x-queue-type": "quorum", key: exchange_name, "x-dead-letter-routing-key": "parking"}
        observed = _observed_queue("q", "v", policy=None, arguments=arguments)
    else:
        key = "dead-letter-exchange"
        policy_settings: dict[str, object] = {key: exchange_name}
        policies = [_policy("regular", "^q$", "quorum_queues", 1, vhost="v", definition=policy_settings)]
        observed = _observed_queue("q", "v", effective_policy_definition=policy_settings)

    definitions = _response(
        rabbit_version="4.3.8",
        vhosts=[_vhost("v", "quorum")],
        queues=[queue, _queue("parking", "v", **{"x-queue-type": "classic"})],
        exchanges=[_exchange(exchange_name, "v", "fanout" if exchange_name else "direct")],
        bindings=[_binding(exchange_name, "parking")] if exchange_name else [],
        policies=policies,
    )
    auditor = ClusterAuditor(definitions, queues=[observed], exchanges=[])
    config = _config(auditor).dead_letter_exchange
    assert config.status == ConfigurationStatus.KNOWN
    assert config.value == exchange_name
    assert config.evidence == (ConfigurationEvidence(source, key, exchange_name),)
    coverages = auditor.dead_letter_coverage()
    coverage = next(row for row in coverages if row.queue.id.name == "q")
    assert coverage.dead_letter_exchange == NodeId("v", exchange_name, NodeKind.EXCHANGE)
    dlx_edges = [edge for edge in auditor.topology.edges if edge.kind == EdgeKind.DEAD_LETTER]
    if source == ConfigurationSource.OBSERVED_QUEUE_ARGUMENT:
        (edge,) = dlx_edges
        assert edge.target == coverage.dead_letter_exchange
        assert edge.routing_key == coverage.routing_key == "parking"
        assert coverage.status == DeadLetterCoverageStatus.DESTINATION_POSSIBLE
        assert coverage.destinations == (NodeId("v", "parking", NodeKind.QUEUE),)
        assert not coverage.issues
    else:
        assert not dlx_edges
        assert coverage.status == DeadLetterCoverageStatus.UNDETERMINED
        assert not coverage.destinations
        assert coverage.issues == (
            DeadLetterRoutingIssue(coverage.queue.id, DeadLetterUncertainty.DLX_ROUTE_NOT_CAPTURED),
        )
    assert coverage in auditor.audit().dead_letter_coverage
    assert next(row for row in auditor.queue_limits() if row.queue.name == "q").dead_letter_coverage == coverage
    restored = ClusterAuditor.from_topology(pickle.loads(pickle.dumps(auditor.topology)))
    assert restored.dead_letter_coverage() == coverages


@pytest.mark.parametrize(
    ("captured_section", "reason"),
    [
        ("policies", ConfigurationUncertainty.OPERATOR_POLICY_DEFINITIONS_NOT_CAPTURED),
        ("parameters", ConfigurationUncertainty.POLICY_DEFINITIONS_NOT_CAPTURED),
    ],
)
def test_omitted_policy_section_cannot_establish_quorum_defaults(
    captured_section: str, reason: ConfigurationUncertainty
) -> None:
    definitions = ClusterDefinitionsResponse.model_validate(
        {"rabbitmq_version": "4.3.8", "queues": [_queue("q", "v", **{"x-queue-type": "quorum"})], captured_section: []}
    )
    config = _config(ClusterAuditor(definitions))
    for setting in (config.delivery_limit, config.delayed_retry_type):
        assert setting.status == ConfigurationStatus.UNKNOWN
        assert setting.uncertainties == (reason,)

    observed = _observed_queue("q", "v", effective_policy_definition={"delivery-limit": 7})
    recovered = _config(ClusterAuditor(definitions, queues=[observed], exchanges=[]))
    assert recovered.delivery_limit.value == 7
    assert recovered.delayed_retry_type.value == "disabled"


def test_regular_operator_and_argument_resolution_retains_each_source() -> None:
    config = _config(
        _auditor(
            arguments={
                "x-delayed-retry-type": "failed",
                "x-delayed-retry-min": 1000,
                "x-delayed-retry-max": 9000,
                "x-delivery-limit": -1,
                "x-dead-letter-exchange": "dlx",
                "x-dead-letter-strategy": "at-least-once",
                "x-consumer-timeout": 120_000,
                "x-consumer-disconnected-timeout": 60_000,
            },
            regular={
                "delayed-retry-type": "all",
                "delayed-retry-min": 2000,
                "delayed-retry-max": 12_000,
                "delivery-limit": 40,
                "overflow": "reject-publish",
                "consumer-timeout": 180_000,
                "consumer-disconnected-timeout": 90_000,
            },
            operator={
                "delayed-retry-type": "returned",
                "delayed-retry-min": 3000,
                "delayed-retry-max": 8000,
                "delivery-limit": 25,
                "overflow": "drop-head",
            },
        )
    )
    assert config.delayed_retry_type.value == "returned"
    assert config.delayed_retry_min.value == 1000
    assert config.delayed_retry_max.value == 8000
    assert config.delivery_limit.value == 25
    assert config.consumer_timeout.value == 120_000
    assert config.consumer_disconnected_timeout.value == 60_000
    assert config.effective_dead_letter_strategy.value == "at-most-once"
    assert {item.source for item in config.delayed_retry_min.evidence} == {
        ConfigurationSource.QUEUE_ARGUMENT,
        ConfigurationSource.REGULAR_POLICY,
        ConfigurationSource.OPERATOR_POLICY,
    }


@pytest.mark.parametrize("retry_type", ["all", "failed", "returned"])
def test_retry_maximum_defaults_to_resolved_minimum(retry_type: str) -> None:
    config = _config(_auditor({"x-delayed-retry-type": retry_type, "x-delayed-retry-min": 1000}))
    assert config.delayed_retry_type.value == retry_type
    assert config.delayed_retry_max.value == 1000
    assert ConfigurationSource.DERIVED in {item.source for item in config.delayed_retry_max.evidence}


@pytest.mark.parametrize("minimum", [None, 0, -1])
def test_retry_without_positive_minimum_is_disabled_with_an_advisory(minimum: int | None) -> None:
    arguments: dict[str, object] = {"x-delayed-retry-type": "all"}
    if minimum is not None:
        arguments["x-delayed-retry-min"] = minimum
    auditor = _auditor(arguments)
    config = _config(auditor)
    assert config.configured_retry_type.value == "all"
    assert config.delayed_retry_type.value == "disabled"
    assert config.delayed_retry_min.status == ConfigurationStatus.NOT_APPLICABLE
    assert QuorumConfigurationIssue.RETRY_DISABLED_WITHOUT_MINIMUM in auditor.quorum_delivery_configuration()[0].issues


@pytest.mark.parametrize("policy", ["regular", "operator"])
def test_missing_policy_selection_stays_unknown_without_blocking_topology(policy: str) -> None:
    settings: dict[str, object] = {"delivery-limit": 3}
    config = _config(
        _auditor(
            regular=settings if policy == "regular" else None,
            operator=settings if policy == "operator" else None,
            observations=False,
        )
    )
    expected = (
        ConfigurationUncertainty.POLICY_SELECTION_NOT_CAPTURED
        if policy == "regular"
        else ConfigurationUncertainty.OPERATOR_POLICY_SELECTION_NOT_CAPTURED
    )
    assert config.delivery_limit.status == ConfigurationStatus.UNKNOWN
    assert config.delivery_limit.uncertainties == (expected,)


def test_effective_policy_observation_resolves_uncaptured_selection() -> None:
    auditor = _auditor(
        operator={"delivery-limit": 3},
        observed_fields={"operator_policy": None, "effective_policy_definition": {"delivery-limit": 3}},
    )
    # A captured contradictory explicit selection must not be silently trusted.
    assert _config(auditor).delivery_limit.uncertainties == (ConfigurationUncertainty.CONFLICTING_EVIDENCE,)
    data = auditor.definitions
    observed = _observed_queue("q", "v", policy=None, effective_policy_definition={"delivery-limit": 3})
    config = _config(ClusterAuditor(data, queues=[observed], exchanges=[]))
    assert config.delivery_limit.value == 3
    assert {item.source for item in config.delivery_limit.evidence} == {ConfigurationSource.EFFECTIVE_POLICY}


@pytest.mark.parametrize("key", ["x-delayed-retry-min", "x-consumer-timeout"])
def test_captured_null_remains_unknown_with_literal_evidence(key: str) -> None:
    config = _config(_auditor({"x-delayed-retry-type": "all", key: None}))
    setting = config.delayed_retry_type if key == "x-delayed-retry-min" else config.consumer_timeout
    assert setting.status == ConfigurationStatus.UNKNOWN
    assert ConfigurationUncertainty.CAPTURED_NULL in setting.uncertainties
    assert any(item.key == key and item.value is None for item in setting.evidence)


@pytest.mark.parametrize(
    "overflow, expected",
    [
        ("drop-head", "at-most-once"),
        ("reject-publish", "at-least-once"),
        ("reject-publish-dlx", "at-most-once"),
    ],
)
def test_dead_letter_strategy_uses_effective_overflow(overflow: str, expected: str) -> None:
    auditor = _auditor(
        {"x-dead-letter-exchange": "dlx", "x-dead-letter-strategy": "at-least-once"},
        regular={"overflow": overflow},
    )
    config = _config(auditor)
    assert config.effective_dead_letter_strategy.value == expected
    assert (QuorumConfigurationIssue.AT_LEAST_ONCE_FALLBACK in auditor.quorum_delivery_configuration()[0].issues) is (
        overflow != "reject-publish"
    )


def test_rabbitmq42_keeps_delivery_limit_and_marks_43_features_inapplicable() -> None:
    config = _config(_auditor({"x-delayed-retry-type": "all", "x-delayed-retry-min": 1000}, version="4.2.9"))
    assert config.delivery_limit.value == 20
    assert config.delivery_count_basis.value == "acquired"
    assert config.delayed_retry_type.status == ConfigurationStatus.NOT_APPLICABLE
    assert config.consumer_timeout.status == ConfigurationStatus.NOT_APPLICABLE
    assert config.consumer_disconnected_timeout.status == ConfigurationStatus.NOT_APPLICABLE
    assert config.configured_retry_type.evidence[0].value == "all"


@pytest.mark.parametrize("version", [None, "4.4.0", "5.0.0", "not-a-version"])
def test_missing_or_unsupported_version_never_invents_effective_settings(version: str | None) -> None:
    config = _config(_auditor({"x-consumer-timeout": 1000}, version=version))
    assert config.consumer_timeout.status == ConfigurationStatus.UNKNOWN
    assert config.delivery_limit.status == ConfigurationStatus.UNKNOWN
    expected = (
        ConfigurationUncertainty.BROKER_VERSION_NOT_CAPTURED
        if version is None
        else ConfigurationUncertainty.UNSUPPORTED_BROKER_VERSION
    )
    assert expected in config.consumer_timeout.uncertainties
    assert config.consumer_timeout.evidence[0].value == 1000
    assert not any(item.source == ConfigurationSource.BROKER_DEFAULT for item in config.delivery_limit.evidence)
    assert not config.delivery_count_basis.evidence


def test_from_topology_and_pickle_retain_configuration_without_source_definitions() -> None:
    auditor = _auditor({"x-delayed-retry-type": "failed", "x-delayed-retry-min": 1000})
    restored = ClusterAuditor.from_topology(pickle.loads(pickle.dumps(auditor.topology)))
    assert restored.quorum_delivery_configuration() == auditor.quorum_delivery_configuration()
    assert restored.audit() == auditor.audit()


def test_manually_built_quorum_graph_retains_unknown_configuration() -> None:
    queue = QueueNode(NodeId(vhost="v", name="q", kind=NodeKind.QUEUE), "quorum", True)
    topology = ClusterTopology(frozenset(), frozenset({queue}), frozenset(), frozenset())
    auditor = ClusterAuditor.from_topology(topology)
    (result,) = auditor.quorum_delivery_configuration()
    assert result.configuration.delivery_limit.uncertainties == (ConfigurationUncertainty.CONFIGURATION_NOT_CAPTURED,)
    assert not result.issues


@pytest.mark.parametrize("key", ["x-delayed-retry-min", "x-delayed-retry-max"])
def test_nonpositive_captured_bounds_with_unknown_policy_do_not_block_audit(key: str) -> None:
    config = _config(
        _auditor({key: 0}, regular={"delayed-retry-type": "all", "delayed-retry-min": 10}, observations=False)
    )
    setting = config.delayed_retry_min if key.endswith("min") else config.delayed_retry_max
    assert setting.status == ConfigurationStatus.UNKNOWN


def test_non_quorum_and_dangling_resources_do_not_create_quorum_entries() -> None:
    auditor = ClusterAuditor(
        _response(
            rabbit_version="4.3.8",
            queues=[_queue("classic", "v", **{"x-queue-type": "classic"})],
        )
    )
    assert auditor.quorum_delivery_configuration() == ()


def test_configuration_isolated_from_source_mutation_and_canonically_ordered() -> None:
    auditor = _auditor({"x-consumer-timeout": 1000}, regular={"consumer-timeout": 2000})
    before = auditor.quorum_delivery_configuration()
    auditor.definitions.queues[0].arguments.root["x-consumer-timeout"] = 9000
    auditor.definitions.policies[0].definition.root["consumer-timeout"] = 8000
    assert auditor.quorum_delivery_configuration() == before
    assert before[0].configuration.consumer_timeout.value == 1000

    queues = [_queue("z", "b", **{"x-queue-type": "quorum"}), _queue("a", "a", **{"x-queue-type": "quorum"})]
    first = ClusterAuditor(_response(rabbit_version="4.3.8", queues=queues))
    second = ClusterAuditor(_response(rabbit_version="4.3.8", queues=queues[::-1]))
    assert first.quorum_delivery_configuration() == second.quorum_delivery_configuration()
    assert [(result.queue.vhost, result.queue.name) for result in first.quorum_delivery_configuration()] == [
        ("a", "a"),
        ("b", "z"),
    ]
