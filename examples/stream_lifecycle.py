"""Create, configure, inspect, and delete a stream through management HTTP."""

from __future__ import annotations

import argparse
import json
import os
from time import monotonic, sleep

from pydantic import SecretStr

from rabbitmq_management_sdk import (
    Config,
    NotFoundError,
    QueueRequest,
    QueueResponse,
    RabbitMQClient,
    RabbitMQError,
    StreamQueueRequest,
)


def verify_retention(
    client: RabbitMQClient, stream: str, policy: str, *, max_age: str, max_length_bytes: int
) -> QueueResponse:
    """Wait for the queue endpoint to report the selected policy and its limits."""
    deadline = monotonic() + 10
    while True:
        queue = client.queues.get(stream, disable_stats=True)
        effective = queue.effective_policy_definition
        if (
            queue.policy == policy
            and effective is not None
            and (effective.max_age, effective.max_length_bytes) == (max_age, max_length_bytes)
        ):
            return queue
        if monotonic() >= deadline:
            raise RabbitMQError(
                f"Stream {stream!r} reported regular policy {queue.policy!r}, "
                f"operator policy {queue.operator_policy!r}, and effective definition "
                f"{effective.root if effective is not None else None!r}"
            )
        sleep(0.25)


def run(client: RabbitMQClient, stream: str, policy: str, *, priority: int) -> None:
    """Run the lifecycle using new stream and policy names in the configured vhost."""
    queues = client.queues
    policies = client.policies
    try:
        queues.get(stream)
    except NotFoundError:
        pass
    else:
        raise RabbitMQError(f"Choose an unused stream name. Queue {stream!r} already exists")
    if any(existing.name == policy for existing in policies.list_by_vhost()):
        raise RabbitMQError(f"Choose an unused policy name. Policy {policy!r} already exists")

    queues.create(
        stream,
        QueueRequest(
            durable=True,
            auto_delete=False,
            arguments=StreamQueueRequest(
                max_age="1D",
                max_length_bytes=1_000_000,
            ),
        ),
    )
    policy_written = False
    try:
        queue = queues.get(stream, disable_stats=True)
        print(
            "Declared stream",
            queue.model_dump_json(
                include={"name", "vhost", "type", "durable", "arguments"}, by_alias=True, exclude_none=True
            ),
        )
        print(
            "Streams in vhost", json.dumps([item.name for item in queues.list_streams(page_size=1, disable_stats=True)])
        )

        max_age, max_length_bytes = "7D", 2_000_000
        policies.set_stream_retention(
            policy,
            stream_name=stream,
            max_age=max_age,
            max_length_bytes=max_length_bytes,
            priority=priority,
        )
        policy_written = True
        print("Regular policy", policies.get(policy).model_dump_json(by_alias=True))
        updated = verify_retention(client, stream, policy, max_age=max_age, max_length_bytes=max_length_bytes)
        print("Verified queue policy", updated.model_dump_json(include={"policy", "effective_policy_definition"}))
    finally:
        try:
            queues.delete(stream)
        finally:
            if policy_written:
                policies.delete(policy)


def main() -> None:
    """Read management credentials from the environment and run the example."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("vhost", help="existing disposable virtual host")
    parser.add_argument("stream", help="unused raw queue name")
    parser.add_argument("policy", help="unused raw regular policy name")
    parser.add_argument("--priority", required=True, type=int, help="priority for the dedicated regular policy")
    args = parser.parse_args()
    try:
        config = Config(
            host=os.environ.get("RABBIT_HOST", "localhost"),
            port=int(os.environ.get("RABBIT_PORT", "15672")),
            username=os.environ["RABBIT_USER"],
            password=SecretStr(os.environ["RABBIT_PASS"]),
            virtual_host=args.vhost,
        )
        with RabbitMQClient(config) as client:
            run(client, args.stream, args.policy, priority=args.priority)
    except KeyError as exc:
        parser.exit(1, f"Set the {exc.args[0]} environment variable.\n")
    except RabbitMQError as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
