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
    RabbitMQError,
    StreamQueueRequest,
)

if TYPE_CHECKING:
    from tests.conftest import RabbitSettings
    from tests.shared.live_broker import ManagementAPI

    from rabbitmq_management_sdk import RabbitMQClient

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


def test_stream_lifecycle_updates_only_the_dedicated_policy(live_client: RabbitMQClient) -> None:
    stream = "z/orders.[eu]+?#%"
    neighbor = f"{stream}-neighbor"
    policy = "retention/eu?#%"
    pattern = r"\Az/orders\.\[eu\]\+\?\#%\z"
    queues = live_client.queues
    policies = live_client.policies
    declared = StreamQueueRequest(max_age="1D", max_length_bytes=1_000_000, stream_max_segment_size=100_000_000)
    queues.create("a-classic", QueueRequest(arguments=ClassicQueueRequest()))
    queues.create("b-quorum", QueueRequest(arguments=QuorumQueueRequest()))
    for name in (stream, neighbor):
        queues.create(name, QueueRequest(arguments=declared))

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
            definition=PolicyDefinition(max_age="2D", max_length_bytes=1_500_000, stream_filter_size_bytes=32),
        ),
    )
    wait_for(lambda: queues.get(stream).policy == policy, "dedicated policy selection")
    policies.set_stream_retention(policy, stream_name=stream, max_age="7D", max_length_bytes=2_000_000, priority=20)
    expected = {"max-age": "7D", "max-length-bytes": 2_000_000, "stream-filter-size-bytes": 32}
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

    queues.delete(stream)
    with pytest.raises(NotFoundError):
        queues.get(stream)
    assert policies.get(policy) == result
    policies.delete(policy)
    with pytest.raises(NotFoundError):
        policies.get(policy)
    assert [queue.name for queue in queues.list_streams(page_size=1)] == [neighbor]


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
