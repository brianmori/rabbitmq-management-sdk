# Audit queue limits and stream retention

A classic queue declares `x-max-length=10`. Its selected regular policy
sets `max-length=3`, and the operator policy sets `max-length=2`. The
effective limit is two messages. A message TTL on that queue controls the
age of messages, while the unused-queue expiry can remain unset.

Use `ClusterAuditor.queue_limits()` or `TopologyAuditReport.queue_limits`
for one immutable `QueueLimits` row per declared queue in canonical
`NodeId` order. Import the result types from `rabbitmq_management_sdk.topology`.
Each row exposes `queue`, `queue_type`, `configuration`, and
`dead_letter_coverage`. The same configuration is retained on
`QueueNode.queue_limits` and works with `from_topology()`.

## Settings and units

| Field on `QueueLimitsConfiguration` | Unit | Meaning |
| --- | --- | --- |
| `max_length` | Messages | Classic or quorum queue length threshold. |
| `max_length_bytes` | Message body bytes | Classic or quorum queue message size threshold. |
| `overflow` | Strategy name | Behavior when a classic or quorum limit is reached. |
| `message_ttl_ms` | Milliseconds | Queue-level time to live for messages. |
| `queue_expiry_ms` | Milliseconds | Lease for an unused classic or quorum queue. |
| `stream_max_age_ms` | Milliseconds | Age threshold for stream segments. |
| `stream_max_length_bytes` | Bytes | Size threshold for the stream log. |
| `stream_max_segment_size_bytes` | Bytes | Evidence for the segment size setting at stream creation. |

Classic length limits count ready messages. Quorum length limits also count
messages pending at-least-once dead lettering. RabbitMQ 4.3 additionally
includes delayed messages in its quorum count. Unacknowledged consumer
deliveries are outside these limits. [RabbitMQ 4.2.8 quorum counters](https://github.com/rabbitmq/rabbitmq-server/blob/v4.2.8/deps/rabbit/src/rabbit_fifo.erl#L2576-L2585)
and [4.3.6 counters](https://github.com/rabbitmq/rabbitmq-server/blob/v4.3.6/deps/rabbit/src/rabbit_fifo.erl#L3323-L3332).

Streams retain an append-only log. Retention removes old segments and keeps at
least one nonempty segment. Queue expiry, message TTL, ready-message limits,
and overflow fields are `not_applicable` to streams. Stream fields are
`not_applicable` to classic and quorum queues. [RabbitMQ stream retention](https://www.rabbitmq.com/docs/4.2/streams#data-retention)
and [queue expiry](https://www.rabbitmq.com/docs/4.2/ttl#queue-ttl).

## Classify stream retention

`ClusterAuditor.stream_retention()` and `TopologyAuditReport.stream_retention`
return one immutable `StreamRetention` row per declared stream in canonical
`NodeId` order. This includes queues whose type was resolved from a captured
vhost default. Each row exposes the queue identity and the same resolved age
and byte settings as `queue_limits()`, with their evidence and uncertainties.
Import `StreamRetention` and `StreamRetentionStatus` from
`rabbitmq_management_sdk.topology`.

| Status | Interpretation |
| --- | --- |
| `configured` | At least one retention setting is `known`, even if the other is `unknown`. |
| `missing` | Both retention settings are explicitly `unset`. |
| `indeterminate` | The capture establishes neither an effective limit nor the absence of both settings. |

A stream declared with `x-max-age: "7D"` has a resolved age limit of
604,800,000 milliseconds when that declaration supplies the effective value.

`stream_max_segment_size_bytes.evidence` can contain the declared
`x-stream-max-segment-size-bytes` argument, the selected regular policy's
`stream-max-segment-size-bytes`, and the reported effective policy entry. A
matching queue observation can supply the argument when the definitions
argument map was omitted. Each entry preserves its wire key, value, source,
and selected policy name where known.

A creation-time policy can override the argument. Current policy selection can
reflect a later change, including removal of a policy that applied at creation.
The supported captures leave the applied segment size `unknown` and retain the
available inputs. Segment size controls the granularity of retention. Only age
and byte limits determine the classification, and sizing thresholds depend on
the caller's workload. [RabbitMQ 4.2 stream settings](https://www.rabbitmq.com/docs/4.2/streams#declaring-a-rabbitmq-stream),
[4.2.9 creation-time precedence](https://github.com/rabbitmq/rabbitmq-server/blob/v4.2.9/deps/rabbit/src/rabbit_stream_queue.erl#L1188-L1226),
and [4.3.8 creation-time precedence](https://github.com/rabbitmq/rabbitmq-server/blob/v4.3.8/deps/rabbit/src/rabbit_stream_queue.erl#L1197-L1235).

## How effective settings are resolved

The versioned resolver supports captured RabbitMQ 4.2 and 4.3. It uses
declaration arguments, broker-selected regular and operator policies, and
the broker's merged `effective_policy_definition` when captured. The
effective policy map describes the policy layer. The queue implementation
then combines that layer with declaration arguments.

| Setting | Regular and operator policy merge | Policy and declaration argument |
| --- | --- | --- |
| Classic/quorum count, bytes, message TTL, queue expiry | Minimum | Minimum |
| Classic overflow | Operator value takes precedence | Declaration argument takes precedence |
| Quorum overflow | Operator value takes precedence | Policy value takes precedence |
| Stream byte retention | Minimum | Policy value takes precedence |
| Stream age retention | Only regular policy supports age | Policy value takes precedence |

Classic and quorum overflow default to `drop-head` when absence is established.
For quorum queues the shared delivery resolver also handles the
`reject-publish-dlx` policy fallback to `drop-head`. The limits row reuses
that exact overflow value and links to the existing DLX coverage, including
dangling exchanges and uncertain routing. [4.2.8 classic resolution](https://github.com/rabbitmq/rabbitmq-server/blob/v4.2.8/deps/rabbit/src/rabbit_amqqueue_process.erl#L492-L507)
and [4.3.6 classic resolution](https://github.com/rabbitmq/rabbitmq-server/blob/v4.3.6/deps/rabbit/src/rabbit_amqqueue_process.erl#L489-L502).

The stream resolver accepts the broker's case-sensitive age suffixes
`Y`, `M`, `D`, `h`, `m`, and `s` with positive integer magnitudes. A
month is 30 days and a year is 365 days. The normalized value is in
milliseconds and the captured duration text remains in the evidence.
[4.2.8 stream resolution](https://github.com/rabbitmq/rabbitmq-server/blob/v4.2.8/deps/rabbit/src/rabbit_stream_queue.erl#L1227-L1267),
[4.3.6 stream resolution](https://github.com/rabbitmq/rabbitmq-server/blob/v4.3.6/deps/rabbit/src/rabbit_stream_queue.erl#L1252-L1292),
and [4.3.6 age validation](https://github.com/rabbitmq/rabbitmq-server/blob/v4.3.6/deps/rabbit/src/rabbit_amqqueue.erl#L1088-L1116).

The numeric policy validators accept zero for message TTL and length limits.
Queue expiry requires a positive value. Declaration message TTL and expiry
also have a broker validation ceiling of ten years. [4.2.8 policy validation and merge](https://github.com/rabbitmq/rabbitmq-server/blob/v4.2.8/deps/rabbit/src/rabbit_policies.erl),
[4.3.6 policy validation and merge](https://github.com/rabbitmq/rabbitmq-server/blob/v4.3.6/deps/rabbit/src/rabbit_policies.erl),
[4.2.8 quorum configuration](https://github.com/rabbitmq/rabbitmq-server/blob/v4.2.8/deps/rabbit/src/rabbit_quorum_queue.erl#L343-L355),
and [4.3.6 quorum configuration](https://github.com/rabbitmq/rabbitmq-server/blob/v4.3.6/deps/rabbit/src/rabbit_quorum_queue.erl#L425-L447).

## Read capture evidence

Each `ConfigurationValue` contains its status, value, evidence, and
uncertainties. Evidence includes wire keys, captured scalar values and nulls,
source, and selected policy names when their definitions supplied values.

- `known` supplies a value, including zero where valid.
- `unset` records established absence of a setting. An explicit empty
  argument or policy map can contribute evidence of absence.
- `unknown` covers omitted maps or policy information, unresolved nulls,
  inconsistent evidence, unsupported broker versions, and plugin types with
  unknown semantics. A missing selection matters when a compatible captured
  policy could supply the setting. A captured effective policy map can
  resolve missing selections. The SDK relies on the broker to match patterns.
- `not_applicable` records a setting outside a supported queue type's
  feature set.

Capture the arguments, regular policies, and runtime parameters, plus the
complete queue and exchange observation pair when selection evidence is
needed. An omitted section or argument map cannot establish absence. A queue
node constructed without limits metadata keeps applicable settings unknown,
with the existing overflow value available for a quorum node that has delivery
metadata. Plugin queue types can be represented in graph values, with
uncertain limits. See the [capture contract](capture-contract.md).

The result describes the supplied snapshot. Publisher-supplied message TTL,
disk usage, time until full, loss rates, and runtime pressure are outside its
scope. Use [DLX coverage](dead-letter-coverage.md) for possible destinations
and [quorum delivery configuration](quorum-queue-auditing.md) for delivery
strategy evidence.

## Try the saved capture

Run from the repository root with the SDK installed:

```python
import json
from pathlib import Path

from rabbitmq_management_sdk import ClusterAuditor, ExchangeResponse, QueueResponse
from rabbitmq_management_sdk.resources.v4.admin.schemas.export_response import ClusterDefinitionsResponse

path = Path("tests/integration/queue-limits/rmq-4.3.6.json")
capture = json.loads(path.read_text(encoding="utf-8"))
auditor = ClusterAuditor(
    ClusterDefinitionsResponse.model_validate(capture["definitions"]),
    queues=[QueueResponse.model_validate(row) for row in capture["queues"]],
    exchanges=[ExchangeResponse.model_validate(row) for row in capture["exchanges"]],
)
rows = {row.queue.name: row.configuration for row in auditor.queue_limits()}
print(rows["orders"].max_length.value, rows["orders"].queue_expiry_ms.status)
(history,) = auditor.stream_retention()
print(history.queue.name, history.status, history.stream_max_length_bytes.value)
print(history.stream_max_segment_size_bytes.status)
```

```text
2 unset
history configured 2000000
unknown
```

The [capture README](../tests/integration/queue-limits/README.md) records
provenance and verification on disposable 4.2.9 and 4.3.6 brokers.
