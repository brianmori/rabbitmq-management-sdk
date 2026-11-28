"""Verify queue-specific precedence and capture uncertainty offline."""

import pickle

import pytest
from tests.shared.parser_fixtures import _observed_queue, _policy, _queue, _response, _vhost

from rabbitmq_management_sdk import ClusterAuditor
from rabbitmq_management_sdk.resources.v4.admin.schemas.export_response import ClusterDefinitionsResponse
from rabbitmq_management_sdk.topology import (
    ConfigurationEvidence,
    ConfigurationSource,
    ConfigurationStatus,
    ConfigurationUncertainty,
    DeadLetterCoverageStatus,
    NodeId,
    NodeKind,
    QueueLimits,
    StreamRetentionStatus,
)

pytestmark = pytest.mark.unit


def _audit(
    queue_type: str,
    *,
    arguments: dict[str, object] | None = None,
    regular: dict[str, object] | None = None,
    operator: dict[str, object] | None = None,
    observed_fields: dict[str, object] | None = None,
    version: str | None = "4.3.8",
) -> ClusterAuditor:
    scope = {"classic": "classic_queues", "quorum": "quorum_queues", "stream": "streams"}[queue_type]
    definitions = _response(
        rabbitmq_version=version,
        queues=[_queue("work", "v", **{"x-queue-type": queue_type, **(arguments or {})})],
        policies=[_policy("regular", ".*", scope, 1, vhost="v", definition=regular)] if regular is not None else [],
        parameters=[
            {
                "component": "operator_policy",
                "vhost": "v",
                "name": "operator",
                "value": {"pattern": ".*", "apply-to": scope, "definition": operator},
            }
        ]
        if operator is not None
        else [],
    )
    if observed_fields is None:
        return ClusterAuditor(definitions)
    observation = _observed_queue(
        "work", "v", type=queue_type, arguments={"x-queue-type": queue_type}, **observed_fields
    )
    return ClusterAuditor(definitions, queues=[observation], exchanges=[])


def _one(auditor: ClusterAuditor) -> QueueLimits:
    (result,) = auditor.queue_limits()
    return result


@pytest.mark.parametrize("version", ["4.2.9", "4.3.8"])
def test_classic_policy_minimum_and_argument_overflow(version: str) -> None:
    auditor = _audit(
        "classic",
        arguments={"x-max-length": 10, "x-max-length-bytes": 1024, "x-message-ttl": 3000, "x-overflow": "drop-head"},
        regular={"max-length": 3, "message-ttl": 4000, "overflow": "reject-publish"},
        operator={"max-length": 2, "max-length-bytes": 512},
        observed_fields={
            "policy": "regular",
            "operator_policy": "operator",
            "effective_policy_definition": {
                "max-length": 2,
                "max-length-bytes": 512,
                "message-ttl": 4000,
                "overflow": "reject-publish",
            },
        },
        version=version,
    )
    config = _one(auditor).configuration

    assert config.broker_version == version
    assert config.max_length.value == 2
    assert config.max_length_bytes.value == 512
    assert config.message_ttl_ms.value == 3000
    assert config.queue_expiry_ms.status == ConfigurationStatus.UNSET
    assert config.overflow.value == "drop-head"
    assert config.stream_max_age_ms.status == ConfigurationStatus.NOT_APPLICABLE
    assert config.stream_max_length_bytes.status == ConfigurationStatus.NOT_APPLICABLE
    assert config.stream_max_segment_size_bytes.status == ConfigurationStatus.NOT_APPLICABLE
    assert set(config.max_length.evidence) == {
        ConfigurationEvidence(ConfigurationSource.QUEUE_ARGUMENT, "x-max-length", 10),
        ConfigurationEvidence(ConfigurationSource.REGULAR_POLICY, "max-length", 3, "regular"),
        ConfigurationEvidence(ConfigurationSource.OPERATOR_POLICY, "max-length", 2, "operator"),
        ConfigurationEvidence(ConfigurationSource.EFFECTIVE_POLICY, "max-length", 2),
    }


def test_quorum_shares_overflow_and_dlx_evidence_through_report_and_graph_transfer() -> None:
    auditor = _audit(
        "quorum",
        arguments={"x-overflow": "reject-publish", "x-expires": 8000, "x-dead-letter-exchange": ""},
        regular={"overflow": "reject-publish-dlx", "expires": 6000},
        operator={"expires": 4000},
        observed_fields={"policy": "regular", "operator_policy": "operator"},
    )
    report = auditor.audit()
    (result,) = report.queue_limits
    assert result.configuration.overflow is report.quorum_delivery_configuration[0].configuration.overflow
    assert result.configuration.overflow.value == "drop-head"
    assert result.configuration.queue_expiry_ms.value == 4000
    assert result.dead_letter_coverage is report.dead_letter_coverage[0]
    assert result.dead_letter_coverage.status == DeadLetterCoverageStatus.MISSING_EXCHANGE
    assert result.dead_letter_coverage.dead_letter_exchange is not None
    assert result.dead_letter_coverage.dead_letter_exchange.name == ""
    restored = ClusterAuditor.from_topology(pickle.loads(pickle.dumps(auditor.topology)))
    assert restored.queue_limits() == auditor.queue_limits() == (result,)


@pytest.mark.parametrize("observed_fields", [None, {}, {"operator_policy": None}])
def test_uncaptured_policy_selection_does_not_establish_a_limit(observed_fields: dict[str, object] | None) -> None:
    config = _one(
        _audit("classic", arguments={"x-max-length": 10}, regular={"max-length": 3}, observed_fields=observed_fields)
    ).configuration
    assert config.max_length.uncertainties == (ConfigurationUncertainty.POLICY_SELECTION_NOT_CAPTURED,)
    assert config.max_length.evidence == (
        ConfigurationEvidence(ConfigurationSource.QUEUE_ARGUMENT, "x-max-length", 10),
    )
    assert config.max_length_bytes.status == ConfigurationStatus.UNSET


def test_merged_policy_observation_can_fill_gaps_and_expose_conflict() -> None:
    config = _one(
        _audit(
            "classic",
            regular={"max-length": 9, "max-length-bytes": 100},
            operator={"max-length-bytes": 50},
            observed_fields={
                "policy": "regular",
                "effective_policy_definition": {"max-length": 9, "max-length-bytes": 110},
            },
        )
    ).configuration
    assert config.max_length.value == 9
    assert config.max_length_bytes.uncertainties == (ConfigurationUncertainty.CONFLICTING_EVIDENCE,)
    assert {item.value for item in config.max_length_bytes.evidence} == {100, 110}


def test_omitted_sections_and_arguments_remain_distinct_from_empty_sections() -> None:
    minimal = {
        "rabbitmq_version": "4.2.9",
        "vhosts": [_vhost("v")],
        "queues": [{"name": "q", "vhost": "v", "durable": True, "auto_delete": False}],
    }
    missing = _one(ClusterAuditor(ClusterDefinitionsResponse.model_validate(minimal))).configuration
    assert set(missing.max_length.uncertainties) == {
        ConfigurationUncertainty.ARGUMENTS_NOT_CAPTURED,
        ConfigurationUncertainty.POLICY_DEFINITIONS_NOT_CAPTURED,
        ConfigurationUncertainty.OPERATOR_POLICY_DEFINITIONS_NOT_CAPTURED,
    }
    complete = {**minimal, "policies": [], "parameters": [], "queues": [_queue("q", "v")]}
    captured = _one(ClusterAuditor(ClusterDefinitionsResponse.model_validate(complete))).configuration
    assert captured.max_length.status == ConfigurationStatus.UNSET
    assert captured.overflow.value == "drop-head"
    assert captured.overflow.evidence == (
        ConfigurationEvidence(ConfigurationSource.BROKER_DEFAULT, "overflow", "drop-head"),
    )


def test_zero_null_and_invalid_expiry_are_distinct() -> None:
    config = _one(
        _audit("classic", arguments={"x-message-ttl": 0, "x-expires": 0, "x-max-length": 0, "x-max-length-bytes": None})
    ).configuration
    assert config.message_ttl_ms.value == config.max_length.value == 0
    assert config.max_length_bytes.uncertainties == (ConfigurationUncertainty.CAPTURED_NULL,)
    assert config.max_length_bytes.evidence[0].value is None
    assert config.queue_expiry_ms.uncertainties == (ConfigurationUncertainty.INVALID_VALUE,)
    assert config.queue_expiry_ms.evidence[0].value == 0


@pytest.mark.parametrize("age, milliseconds", [("1M", 2_592_000_000), ("1m", 60_000), ("7D", 604_800_000)])
def test_stream_policy_replaces_argument_and_keeps_broker_age_units(age: str, milliseconds: int) -> None:
    config = _one(
        _audit(
            "stream",
            arguments={"x-max-age": "1s", "x-max-length-bytes": 100},
            regular={"max-age": age, "max-length-bytes": 900},
            operator={"max-length-bytes": 400},
            observed_fields={"policy": "regular", "operator_policy": "operator"},
        )
    ).configuration
    assert config.stream_max_age_ms.value == milliseconds
    assert config.stream_max_length_bytes.value == 400
    assert ConfigurationEvidence(ConfigurationSource.REGULAR_POLICY, "max-age", age, "regular") in (
        config.stream_max_age_ms.evidence
    )
    for setting in (
        config.max_length,
        config.max_length_bytes,
        config.message_ttl_ms,
        config.queue_expiry_ms,
        config.overflow,
    ):
        assert setting.status == ConfigurationStatus.NOT_APPLICABLE


@pytest.mark.parametrize("age", ["0h", "7d", "1ms", "4"])
def test_invalid_stream_age_is_unknown(age: str) -> None:
    config = _one(_audit("stream", arguments={"x-max-age": age})).configuration
    assert config.stream_max_age_ms.uncertainties == (ConfigurationUncertainty.INVALID_VALUE,)
    assert config.stream_max_age_ms.evidence[0].value == age


@pytest.mark.parametrize(
    "arguments,age_status,bytes_status,status",
    [
        (
            {"x-max-age": "7D", "x-max-length-bytes": None},
            ConfigurationStatus.KNOWN,
            ConfigurationStatus.UNKNOWN,
            StreamRetentionStatus.CONFIGURED,
        ),
        (
            {"x-max-length-bytes": 0},
            ConfigurationStatus.UNSET,
            ConfigurationStatus.KNOWN,
            StreamRetentionStatus.CONFIGURED,
        ),
        ({}, ConfigurationStatus.UNSET, ConfigurationStatus.UNSET, StreamRetentionStatus.MISSING),
        (
            {"x-max-age": None},
            ConfigurationStatus.UNKNOWN,
            ConfigurationStatus.UNSET,
            StreamRetentionStatus.INDETERMINATE,
        ),
    ],
)
def test_stream_retention_classifies_resolved_settings_without_losing_evidence(
    arguments: dict[str, object],
    age_status: ConfigurationStatus,
    bytes_status: ConfigurationStatus,
    status: StreamRetentionStatus,
) -> None:
    auditor = _audit("stream", arguments=arguments)
    (result,) = auditor.stream_retention()
    config = _one(auditor).configuration
    assert result.status == status
    assert result.stream_max_age_ms is config.stream_max_age_ms
    assert result.stream_max_age_ms.status == age_status
    assert result.stream_max_length_bytes is config.stream_max_length_bytes
    assert result.stream_max_length_bytes.status == bytes_status


@pytest.mark.parametrize("declared_size", [None, 100_000_000])
def test_stream_retention_keeps_current_policy_segment_evidence_without_claiming_it_applied(
    declared_size: int | None,
) -> None:
    policy = {"max-age": "7D", "stream-max-segment-size-bytes": 50_000_000}
    auditor = _audit(
        "stream",
        arguments={"x-stream-max-segment-size-bytes": declared_size} if declared_size is not None else {},
        regular=policy,
        observed_fields={"policy": "regular", "operator_policy": None, "effective_policy_definition": policy},
    )
    (result,) = auditor.stream_retention()
    assert result.status == StreamRetentionStatus.CONFIGURED
    assert result.stream_max_age_ms.value == 604_800_000
    assert result.stream_max_age_ms.status == ConfigurationStatus.KNOWN
    assert result.stream_max_length_bytes.status == ConfigurationStatus.UNSET
    segment_size = result.stream_max_segment_size_bytes
    assert segment_size is _one(auditor).configuration.stream_max_segment_size_bytes
    assert segment_size.status == ConfigurationStatus.UNKNOWN
    assert segment_size.value is None
    assert segment_size.uncertainties == (ConfigurationUncertainty.STREAM_CREATION_CONFIGURATION_NOT_CAPTURED,)
    expected = {
        ConfigurationEvidence(
            ConfigurationSource.REGULAR_POLICY, "stream-max-segment-size-bytes", 50_000_000, "regular"
        ),
        ConfigurationEvidence(ConfigurationSource.EFFECTIVE_POLICY, "stream-max-segment-size-bytes", 50_000_000),
    }
    if declared_size is not None:
        expected.add(
            ConfigurationEvidence(ConfigurationSource.QUEUE_ARGUMENT, "x-stream-max-segment-size-bytes", declared_size)
        )
    assert set(segment_size.evidence) == expected


def test_stream_retention_uses_resolved_types_and_full_identity_order_after_graph_transfer() -> None:
    auditor = ClusterAuditor(
        _response(
            rabbitmq_version="4.2.9",
            global_parameters=[{"name": "internal_cluster_id", "value": "stream-cluster"}],
            vhosts=[_vhost("b", "stream"), _vhost("a")],
            queues=[
                _queue("history", "b", **{"x-stream-max-segment-size-bytes": 100_000_000}),
                _queue("classic", "b", **{"x-queue-type": "classic"}),
                _queue("quorum", "b", **{"x-queue-type": "quorum"}),
                _queue("history", "a", **{"x-queue-type": "stream", "x-max-age": "7D"}),
            ],
        )
    )
    explicit, defaulted = auditor.stream_retention()
    assert explicit.queue == NodeId("a", "history", NodeKind.QUEUE, cluster_id="stream-cluster")
    assert defaulted.queue == NodeId("b", "history", NodeKind.QUEUE, cluster_id="stream-cluster")
    assert explicit.status == StreamRetentionStatus.CONFIGURED
    assert defaulted.status == StreamRetentionStatus.MISSING
    segment_size = defaulted.stream_max_segment_size_bytes
    assert segment_size.status == ConfigurationStatus.UNKNOWN
    assert segment_size.evidence == (
        ConfigurationEvidence(ConfigurationSource.QUEUE_ARGUMENT, "x-stream-max-segment-size-bytes", 100_000_000),
    )
    restored = ClusterAuditor.from_topology(pickle.loads(pickle.dumps(auditor.topology)))
    assert restored.stream_retention() == auditor.audit().stream_retention == (explicit, defaulted)


def test_stream_retention_keeps_observed_segment_argument_when_declaration_arguments_are_omitted() -> None:
    queue = _queue("history", "v")
    del queue["arguments"]
    definitions = _response(rabbitmq_version="4.3.8", vhosts=[_vhost("v", "stream")], queues=[queue])
    observed = _observed_queue(
        "history",
        "v",
        type="stream",
        arguments={"x-queue-type": "stream", "x-stream-max-segment-size-bytes": 100_000_000},
    )
    (result,) = ClusterAuditor(definitions, queues=[observed], exchanges=[]).stream_retention()
    assert result.status == StreamRetentionStatus.INDETERMINATE
    assert result.stream_max_age_ms.uncertainties == (ConfigurationUncertainty.ARGUMENTS_NOT_CAPTURED,)
    assert result.stream_max_segment_size_bytes.status == ConfigurationStatus.UNKNOWN
    assert result.stream_max_segment_size_bytes.evidence == (
        ConfigurationEvidence(
            ConfigurationSource.OBSERVED_QUEUE_ARGUMENT, "x-stream-max-segment-size-bytes", 100_000_000
        ),
    )


@pytest.mark.parametrize(
    "version, reason",
    [
        (None, ConfigurationUncertainty.BROKER_VERSION_NOT_CAPTURED),
        ("4.4.0", ConfigurationUncertainty.UNSUPPORTED_BROKER_VERSION),
    ],
)
def test_unknown_version_keeps_argument_evidence(version: str | None, reason: ConfigurationUncertainty) -> None:
    config = _one(_audit("classic", arguments={"x-max-length": 10}, version=version)).configuration
    assert config.max_length.uncertainties == (reason,)
    assert config.max_length.evidence[0].value == 10


def test_plugin_queue_has_unknown_semantics_and_retains_argument_evidence() -> None:
    vhost = _vhost("v", default_queue_type="x-plugin")
    auditor = ClusterAuditor(
        _response(rabbitmq_version="4.3.8", vhosts=[vhost], queues=[_queue("work", "v", **{"x-max-length": 10})])
    )
    result = _one(auditor)
    assert result.queue_type == "x-plugin"
    assert result.configuration.max_length.uncertainties == (ConfigurationUncertainty.UNSUPPORTED_QUEUE_TYPE,)
    assert result.configuration.max_length.evidence[0].value == 10
    assert result.configuration.stream_max_age_ms.status == ConfigurationStatus.UNKNOWN
    assert result.dead_letter_coverage.status == DeadLetterCoverageStatus.UNDETERMINED
