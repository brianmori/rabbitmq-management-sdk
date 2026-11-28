# Quorum queue configuration on RabbitMQ 4.3

A quorum queue stores messages in a replicated, durable queue. RabbitMQ 4.3
adds delayed retry and queue-owned consumer timeouts: returned messages can
wait before another delivery attempt, and unacknowledged messages can be
returned after a timeout. These settings do not guarantee runtime delivery.

The v4 managers serve **both RabbitMQ 4.2 and 4.3**. A manager with a known
broker version raises `UnsupportedBrokerFeatureError` before sending delayed
retry or disconnected-consumer timeout settings to a broker older than 4.3.0.
The `consumer_timeout` queue argument and regular policy setting remain
available for 4.2's channel-level timeout semantics. Optional fields default
to `None`. Both serialization modes send `arguments["x-queue-type"]` explicitly.
[RabbitMQ 4.2 timeout handling](https://github.com/rabbitmq/rabbitmq-server/blob/v4.2.9/deps/rabbit/src/rabbit_channel.erl#L2678-L2691)

## Values, units, and defaults

| Python field | Queue wire key | Policy wire key | Request values | Unconfigured broker default |
| --- | --- | --- | --- | --- |
| `delayed_retry_type` | `x-delayed-retry-type` | `delayed-retry-type` | `DelayedRetryType`: `disabled`, `all`, `failed`, `returned` | `disabled` |
| `delayed_retry_min` | `x-delayed-retry-min` | `delayed-retry-min` | Positive integer milliseconds | No minimum; retry stays disabled |
| `delayed_retry_max` | `x-delayed-retry-max` | `delayed-retry-max` | Positive integer milliseconds | Resolved minimum |
| `consumer_timeout` | `x-consumer-timeout` | `consumer-timeout` | Nonnegative integer milliseconds | Global setting, normally 1,800,000 ms (30 minutes) |
| `consumer_disconnected_timeout` | `x-consumer-disconnected-timeout` | `consumer-disconnected-timeout` | Nonnegative integer milliseconds | Global setting, normally 60,000 ms (60 seconds) |

Requests reject booleans, fractional values, numeric strings, and duration
strings for these durations. Zero is accepted for timeouts, matching broker
validation; it is not an SDK spelling for disabling timeouts. Broker defaults
are not inserted into requests or captures. See the
[4.3 timeout guide](https://www.rabbitmq.com/docs/4.3/quorum-queues#consumer-timeout),
[policy validation](https://github.com/rabbitmq/rabbitmq-server/blob/v4.3.6/deps/rabbit/src/rabbit_policies.erl#L79-L89),
and [queue argument validation](https://github.com/rabbitmq/rabbitmq-server/blob/v4.3.6/deps/rabbit/src/rabbit_amqqueue.erl#L896-L946).

`all` delays every return; `failed` delays failed deliveries; `returned` delays
returns that do not increment the failure count. The delay is
`min(minimum * max(1, delivery_count), maximum)`. Omitting the maximum produces
a fixed delay equal to the minimum. See the
[4.3.6 retry implementation](https://github.com/rabbitmq/rabbitmq-server/blob/v4.3.6/deps/rabbit/src/rabbit_fifo.erl#L2306-L2324).

Regular and operator policy definitions require a minimum when retry is
enabled. Whenever both bounds are present, the maximum must be at least the
minimum, including with `disabled`. Minimum-only and maximum-only definitions
are accepted. The SDK mirrors these
[policy constraints](https://github.com/rabbitmq/rabbitmq-server/blob/v4.3.6/deps/rabbit/src/rabbit_quorum_queue.erl#L207-L246).

Queue arguments have no equivalent cross-field rejection in 4.3.6. Individual
fields are allowed so a policy can supply the remaining configuration. The
broker resolves settings per key: retry type uses policy precedence, numeric
bounds use the smaller argument/policy value. Without a positive resolved
minimum, retry is disabled. The SDK requires positive request delays even
though 4.3.6 validates those queue arguments less strictly than policy values.
Captured responses do not enforce request positivity or cross-field rules.
See [configuration resolution](https://github.com/rabbitmq/rabbitmq-server/blob/v4.3.6/deps/rabbit/src/rabbit_quorum_queue.erl#L1859-L1895).

## Declare a queue

This complete example requires a reachable RabbitMQ 4.3 Management API, a `/`
vhost, and Basic Auth credentials with queue declaration permissions. Set
`RABBIT_USER` and `RABBIT_PASS` in the environment. It creates a durable queue
named `orders.retry`.

```python
import os

from rabbitmq_management_sdk import (
    Config,
    DelayedRetryType,
    QueueRequest,
    QuorumQueueRequest,
    RabbitMQClient,
)

config = Config(
    host="localhost",
    port=15672,
    username=os.environ["RABBIT_USER"],
    password=os.environ["RABBIT_PASS"],
    virtual_host="/",
)
with RabbitMQClient(config) as client:
    client.queues.create(
        "orders.retry",
        QueueRequest(
            arguments=QuorumQueueRequest(
                delayed_retry_type=DelayedRetryType.FAILED,
                delayed_retry_min=1000,
                delayed_retry_max=30_000,
                consumer_timeout=120_000,
                consumer_disconnected_timeout=60_000,
            )
        ),
    )
    observed = client.queues.get("orders.retry")
    print(observed.arguments.model_dump(by_alias=True, exclude_none=True))
```

`QuorumQueueResponse` exposes the corresponding typed attributes. Definitions
exports expose them through `ExportQueueArguments`; policy responses and
exports use `PolicyDefinitionResponse` and `ExportPolicySettings`. Those
response maps preserve unknown JSON entries, exact wire keys, and captured
nulls. Omit `exclude_none=True` when dumping maps to retain null evidence.

## Configure through a policy

This independent example uses the same connection prerequisites and requires
policy management permissions. It creates a policy and a matching queue.
Policies can be updated without redeclaring the queue; RabbitMQ selects which
regular policy applies. Timeout keys below belong to regular policies; retry
keys are also supported in 4.3 operator policies.

```python
import os

from rabbitmq_management_sdk import (
    Config,
    DelayedRetryType,
    PolicyApplyTo,
    PolicyDefinition,
    PolicyRequest,
    QueueRequest,
    QuorumQueueRequest,
    RabbitMQClient,
)

config = Config(
    host="localhost",
    port=15672,
    username=os.environ["RABBIT_USER"],
    password=os.environ["RABBIT_PASS"],
    virtual_host="/",
)
with RabbitMQClient(config) as client:
    client.policies.create(
        "orders-retry",
        PolicyRequest(
            pattern=r"^orders\.work$",
            definition=PolicyDefinition(
                delayed_retry_type=DelayedRetryType.ALL,
                delayed_retry_min=1000,
                delayed_retry_max=30_000,
                consumer_timeout=120_000,
                consumer_disconnected_timeout=60_000,
            ),
            priority=10,
            apply_to=PolicyApplyTo.QUORUM_QUEUES,
        ),
    )
    client.queues.create("orders.work", QueueRequest(arguments=QuorumQueueRequest()))
    print(client.policies.get("orders-retry").definition.delayed_retry_min)
```

## Related 4.3 behavior changes

RabbitMQ 4.3 rejects non-durable, non-exclusive queue declarations by default.
Use durable queues or connection-exclusive transient queues. Quorum queues
remain durable and non-exclusive. The SDK does not add a blanket restriction
that would alter 4.2 behavior. See the
[4.3.0 compatibility notes](https://github.com/rabbitmq/rabbitmq-server/blob/v4.3.6/release-notes/4.3.0.md#deprecated-features-are-now-disabled-by-default).

In 4.3, timeout handling lives in quorum queues; classic queues and streams no
longer evaluate consumer timeouts. Supported protocols include AMQP 0.9.1,
AMQP 1.0, and MQTT; the stream protocol is excluded. A queue timeout applies
to its consumers; a disconnected timeout concerns an unreachable consumer
node during a partition. See the
[release notes](https://github.com/rabbitmq/rabbitmq-server/blob/v4.3.6/release-notes/4.3.0.md#consumer-timeouts-are-no-longer-evaluated-for-classic-queues-and-streams).

The delivery limit now counts failed deliveries rather than acquisitions.
`basic.reject`, failed AMQP 1.0 modifications, and consumer crashes increment
the failure count; `basic.nack` and non-failed returns do not. The new
`x-acquired-count` header tracks assignments separately. This affects the
interaction between delivery limits and retry selectors. See
[4.3 poison-message handling](https://www.rabbitmq.com/docs/4.3/quorum-queues#poison-message-handling).

## Evidence and limitations

The [sanitized 4.3.6 capture](../tests/integration/rmq-4.3/quorum-configuration/README.md)
records actual declaration, policy, and definitions responses. Live tests
check all four retry types and both serialization modes, plus operator retry
policies. These tests are gated to 4.3; existing 4.2 tests retain their coverage.
They verify configuration read-back, without measuring retry timing, timeout
firing, or partition recovery.

Topology regression tests cover all retry types from arguments and policy,
both with a DLX cycle and without a DLX. Route edges and cycle paths remain
unchanged: retry and timeout settings neither create route edges nor prove
that a cycle terminates. The captured configuration also loads through
`ClusterAuditor.from_files()` without requiring observations for a policy
that sets no DLX or alternate-exchange routes.

The auditor retains these values in its `definitions` and frozen
`QueueNode.quorum_delivery` metadata. The public
`quorum_delivery_configuration()` analysis also survives `from_topology()`.
See [quorum queue auditing](quorum-queue-auditing.md) for effective settings,
sources, uncertainty, and the distinction between delivery configuration and
cycle termination.

Weaker broker validation of retry queue arguments is separate from the SDK
defect fixed here: supported Python request fields were silently discarded.
The unrelated [non-ASCII vhost export limitation](known-broker-limitations.md)
remains a broker issue with its existing narrow expected failure.
