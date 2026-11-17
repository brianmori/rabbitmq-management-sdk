"""Resolve quorum audit evidence without inferring broker or consumer state."""

from __future__ import annotations

import pickle
from typing import TYPE_CHECKING

import pytest
from tests.shared.parser_fixtures import _exchange, _observed_queue, _policy, _queue, _response

from rabbitmq_management_sdk import ClusterAuditor
from rabbitmq_management_sdk.topology import (
    ClusterTopology,
    ConfigurationSource,
    ConfigurationStatus,
    ConfigurationUncertainty,
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
    version: str | None = "4.3.6",
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
    assert next(iter(auditor.topology.queues)).quorum_delivery == result.configuration
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
            rabbit_version="4.3.6",
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
    first = ClusterAuditor(_response(rabbit_version="4.3.6", queues=queues))
    second = ClusterAuditor(_response(rabbit_version="4.3.6", queues=queues[::-1]))
    assert first.quorum_delivery_configuration() == second.quorum_delivery_configuration()
    assert [(result.queue.vhost, result.queue.name) for result in first.quorum_delivery_configuration()] == [
        ("a", "a"),
        ("b", "z"),
    ]
