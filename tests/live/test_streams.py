"""Management HTTP stream lifecycle on the pinned RabbitMQ brokers."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

import pytest
from tests.shared.live_broker import wait_for

from rabbitmq_management_sdk import (
    BadRequestError,
    ClassicQueueRequest,
    NotFoundError,
    PolicyApplyTo,
    PolicyDefinition,
    PolicyRequest,
    QueueRequest,
    QuorumQueueRequest,
    RabbitMQClient,
    RabbitMQError,
    StreamQueueRequest,
)
from rabbitmq_management_sdk.topology import (
    ConfigurationEvidence,
    ConfigurationSource,
    StreamRetentionStatus,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.conftest import RabbitSettings
    from tests.shared.live_broker import ManagementAPI

    from rabbitmq_management_sdk import VhostRequest
    from rabbitmq_management_sdk.topology import StreamRetention

pytestmark = pytest.mark.live


def test_stream_retention_age_syntax(
    live_client: RabbitMQClient, live_vhost: str, management_api: ManagementAPI
) -> None:
    vhost = quote(live_vhost, safe="")
    with pytest.raises(BadRequestError, match="invalid_max_age"):
        management_api.request(
            "PUT",
            f"queues/{vhost}/lowercase-day",
            {"durable": True, "arguments": {"x-queue-type": "stream", "x-max-age": "7d"}},
        )
    with pytest.raises(BadRequestError, match="not a valid max age"):
        management_api.request(
            "PUT",
            f"policies/{vhost}/lowercase-day",
            {"pattern": "^age$", "apply-to": "streams", "priority": 1, "definition": {"max-age": "7d"}},
        )

    live_client.queues.create("age", QueueRequest(arguments=StreamQueueRequest(max_age="7D")))
    assert live_client.queues.get("age").arguments.model_dump(by_alias=True)["x-max-age"] == "7D"
    live_client.policies.set_stream_retention(
        "age-upper", stream_name="age", max_age="7D", max_length_bytes=1_000_000, priority=1
    )
    assert live_client.policies.get("age-upper").definition.max_age == "7D"


def test_stream_retention_refuses_shared_and_competing_policies(live_client: RabbitMQClient) -> None:
    stream = "orders"
    policy = "dedicated-retention"
    queues = live_client.queues
    policies = live_client.policies

    shared_name = "shared-streams"
    policies.create(
        shared_name,
        PolicyRequest(
            pattern=r"^orders",
            apply_to=PolicyApplyTo.STREAMS,
            priority=10,
            definition=PolicyDefinition(max_age="2D"),
        ),
    )
    queues.create(stream, QueueRequest(arguments=StreamQueueRequest()))
    shared = policies.get(shared_name)
    with pytest.raises(RabbitMQError, match="selects regular policy"):
        policies.set_stream_retention(policy, stream_name=stream, max_age="7D", max_length_bytes=2_000_000, priority=20)
    with pytest.raises(RabbitMQError, match="different pattern"):
        policies.set_stream_retention(
            shared_name, stream_name=stream, max_age="7D", max_length_bytes=2_000_000, priority=10
        )
    assert policies.get(shared_name) == shared
    with pytest.raises(NotFoundError):
        policies.get(policy)


def test_stream_lifecycle_updates_only_the_dedicated_policy(live_client: RabbitMQClient, live_vhost: str) -> None:
    stream = "z/orders.[eu]+?#%"
    neighbor = f"{stream}-neighbor"
    policy = "retention/eu?#%"
    max_length_bytes = 2_000_000
    pattern = r"\Az/orders\.\[eu\]\+\?\#%\z"
    queues = live_client.queues
    policies = live_client.policies
    queues.create("a-classic", QueueRequest(arguments=ClassicQueueRequest()))
    queues.create("b-quorum", QueueRequest(arguments=QuorumQueueRequest()))
    queues.create(stream, QueueRequest(arguments=StreamQueueRequest(stream_max_segment_size=100_000_000)))
    queues.create(
        neighbor,
        QueueRequest(arguments=StreamQueueRequest(max_age="1D", max_length_bytes=1_000_000)),
    )

    before = queues.get(stream)
    assert before.durable and not before.auto_delete
    assert before.type == "stream"
    assert queues.list_page_by_vhost(page_size=1).page_count == 4
    assert [queue.name for queue in queues.list_streams(page_size=1)] == [stream, neighbor]
    policies.set_stream_retention(
        "neighbor-retention", stream_name=neighbor, max_age="3D", max_length_bytes=3_000_000, priority=20
    )
    neighbor_before = queues.get(neighbor, disable_stats=True)
    assert neighbor_before.policy == "neighbor-retention"
    assert neighbor_before.effective_policy_definition is not None
    assert neighbor_before.effective_policy_definition.root == {"max-age": "3D", "max-length-bytes": 3_000_000}

    policies.create(
        policy,
        PolicyRequest(
            pattern=pattern,
            apply_to=PolicyApplyTo.STREAMS,
            priority=20,
            definition=PolicyDefinition(stream_filter_size_bytes=32),
        ),
    )
    wait_for(lambda: queues.get(stream).policy == policy, "dedicated policy selection")
    retention_before = _capture_stream_retention(live_client)
    assert retention_before[live_vhost, stream].status == StreamRetentionStatus.MISSING
    assert retention_before[live_vhost, neighbor].status == StreamRetentionStatus.CONFIGURED

    policies.set_stream_retention(
        policy, stream_name=stream, max_age="7D", max_length_bytes=max_length_bytes, priority=20
    )
    expected = {"max-age": "7D", "max-length-bytes": max_length_bytes, "stream-filter-size-bytes": 32}
    result = policies.get(policy)
    assert (result.pattern, result.apply_to, result.priority, result.definition.root) == (
        pattern,
        "streams",
        20,
        expected,
    )

    def is_effective() -> bool:
        updated = queues.get(stream)
        assert updated.arguments == before.arguments
        return (
            updated.policy == policy
            and updated.effective_policy_definition is not None
            and updated.effective_policy_definition.root == expected
        )

    wait_for(is_effective, "updated stream retention in queue metadata")
    neighbor_after = queues.get(neighbor, disable_stats=True)
    assert neighbor_after.arguments == neighbor_before.arguments
    assert neighbor_after.policy == neighbor_before.policy
    assert neighbor_after.effective_policy_definition == neighbor_before.effective_policy_definition

    retention_after = _capture_stream_retention(live_client)
    _assert_seven_day_retention(retention_after[live_vhost, stream], policy, max_length_bytes)
    assert retention_after[live_vhost, neighbor] == retention_before[live_vhost, neighbor]

    queues.delete(stream)
    with pytest.raises(NotFoundError):
        queues.get(stream)
    assert policies.get(policy) == result
    policies.delete(policy)
    with pytest.raises(NotFoundError):
        policies.get(policy)
    assert [queue.name for queue in queues.list_streams(page_size=1)] == [neighbor]


@pytest.mark.parametrize(
    "declaration,status,age_ms",
    [
        pytest.param(StreamQueueRequest(), StreamRetentionStatus.MISSING, None, id="no-declared-retention"),
        pytest.param(
            StreamQueueRequest(max_age="2D", max_length_bytes=1_000_000),
            StreamRetentionStatus.CONFIGURED,
            172_800_000,
            id="declared-retention",
        ),
    ],
)
def test_stream_retention_returns_to_declaration_after_policy_removal(
    live_client: RabbitMQClient,
    live_vhost: str,
    declaration: StreamQueueRequest,
    status: StreamRetentionStatus,
    age_ms: int | None,
) -> None:
    stream, policy = "history", "retention"
    live_client.queues.create(stream, QueueRequest(arguments=declaration))
    before = _capture_stream_retention(live_client)[live_vhost, stream]

    live_client.policies.set_stream_retention(
        policy, stream_name=stream, max_age="7D", max_length_bytes=2_000_000, priority=20
    )
    wait_for(lambda: live_client.queues.get(stream).policy == policy, "retention policy selection")
    configured = _capture_stream_retention(live_client)[live_vhost, stream]
    _assert_seven_day_retention(configured, policy, 2_000_000)

    live_client.policies.delete(policy)
    wait_for(lambda: live_client.queues.get(stream).policy is None, "retention policy removal")
    restored = _capture_stream_retention(live_client)[live_vhost, stream]

    assert restored == before
    assert restored.status == status
    assert restored.stream_max_age_ms.value == age_ms
    assert restored.stream_max_length_bytes.value == declaration.max_length_bytes
    if declaration.max_age is not None:
        assert ConfigurationEvidence(ConfigurationSource.QUEUE_ARGUMENT, "x-max-age", declaration.max_age) in (
            restored.stream_max_age_ms.evidence
        )
        assert (
            ConfigurationEvidence(
                ConfigurationSource.QUEUE_ARGUMENT, "x-max-length-bytes", declaration.max_length_bytes
            )
            in restored.stream_max_length_bytes.evidence
        )


def test_same_named_streams_and_retention_policies_stay_in_their_vhosts(
    live_client: RabbitMQClient,
    live_vhost: str,
    rabbit_config: RabbitSettings,
    vhost_factory: Callable[[VhostRequest | None], str],
) -> None:
    other_vhost = vhost_factory(None)
    stream, policy = "history", "retention"
    with RabbitMQClient(rabbit_config.client_config(virtual_host=other_vhost)) as other_client:
        for client in (live_client, other_client):
            client.queues.create(stream, QueueRequest(arguments=StreamQueueRequest()))

        live_client.policies.set_stream_retention(
            policy, stream_name=stream, max_age="1D", max_length_bytes=1_000_000, priority=20
        )
        other_client.policies.set_stream_retention(
            policy, stream_name=stream, max_age="3D", max_length_bytes=3_000_000, priority=20
        )
        wait_for(
            lambda: live_client.queues.get(stream).policy == other_client.queues.get(stream).policy == policy,
            "retention policy selection in both vhosts",
        )
        before = _capture_stream_retention(live_client)
        first, other = before[live_vhost, stream], before[other_vhost, stream]
        assert (first.stream_max_age_ms.value, first.stream_max_length_bytes.value) == (86_400_000, 1_000_000)
        assert (other.stream_max_age_ms.value, other.stream_max_length_bytes.value) == (259_200_000, 3_000_000)

        live_client.policies.set_stream_retention(
            policy, stream_name=stream, max_age="7D", max_length_bytes=2_000_000, priority=20
        )

        def updated_policy_is_effective() -> bool:
            queue = live_client.queues.get(stream)
            definition = queue.effective_policy_definition
            return (
                queue.policy == policy
                and definition is not None
                and definition.root
                == {
                    "max-age": "7D",
                    "max-length-bytes": 2_000_000,
                }
            )

        wait_for(updated_policy_is_effective, "updated retention policy in the target vhost")
        after = _capture_stream_retention(live_client)
        _assert_seven_day_retention(after[live_vhost, stream], policy, 2_000_000)
        assert after[other_vhost, stream] == other
        assert other_client.queues.get(stream).policy == policy
        for client, vhost in ((live_client, live_vhost), (other_client, other_vhost)):
            (listed,) = client.queues.list_streams(page_size=1)
            assert (listed.vhost, listed.name) == (vhost, stream)


def _capture_stream_retention(client: RabbitMQClient) -> dict[tuple[str, str], StreamRetention]:
    return {
        (row.queue.vhost, row.queue.name): row for row in client.collect_audit_capture().to_auditor().stream_retention()
    }


def _assert_seven_day_retention(retention: StreamRetention, policy: str, max_length_bytes: int) -> None:
    assert retention.status == StreamRetentionStatus.CONFIGURED
    age = retention.stream_max_age_ms
    size = retention.stream_max_length_bytes
    assert age.value == 604_800_000
    assert size.value == max_length_bytes
    assert ConfigurationEvidence(ConfigurationSource.REGULAR_POLICY, "max-age", "7D", policy) in age.evidence
    assert ConfigurationEvidence(ConfigurationSource.REGULAR_POLICY, "max-length-bytes", max_length_bytes, policy) in (
        size.evidence
    )


def test_stream_lifecycle_example(live_client: RabbitMQClient, live_vhost: str, rabbit_config: RabbitSettings) -> None:
    example = Path(__file__).resolve().parents[2] / "examples" / "stream_lifecycle.py"
    stream, policy = "example/events.[eu]+?#%", "example/retention?#%"
    result = subprocess.run(
        [sys.executable, str(example), live_vhost, stream, policy, "--priority", "20"],
        env=os.environ
        | {
            "RABBIT_HOST": rabbit_config.host,
            "RABBIT_PORT": str(rabbit_config.port),
            "RABBIT_USER": rabbit_config.username,
            "RABBIT_PASS": rabbit_config.password,
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Verified queue policy" in result.stdout
    with pytest.raises(NotFoundError):
        live_client.queues.get(stream)
    with pytest.raises(NotFoundError):
        live_client.policies.get(policy)
