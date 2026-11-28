# Audit quorum delivery configuration

A quorum queue can have a dead-letter route back to itself. Retry backoff can
slow some redeliveries, and a delivery limit can bound counted failures. Neither
setting proves that every message stops repeating. The SDK retains the cycle
and exposes queue delivery evidence alongside it.

`ClusterAuditor.quorum_delivery_configuration()` and
`report.quorum_delivery_configuration` return one frozen
`QuorumQueueConfiguration` per declared quorum queue, in canonical node order.
The `configuration` is also retained on `QueueNode.quorum_delivery` as an
optional, frozen `QuorumDeliveryConfiguration`.

Configuration survives trusted same-version pickle transfer and
`ClusterAuditor.from_topology()`. It changes resource attributes, equality, and
hashing without changing `NodeId`, edges, or cycle paths. A manually constructed
quorum node without this metadata produces unknown settings.

## Values and evidence

Every `ConfigurationValue` exposes `value`, `status`, `evidence`, and
`uncertainties`. Status distinguishes `known`, `unset` (absence established),
`unknown`, and `not_applicable`. Unknown never means disabled. Evidence retains
exact argument/policy keys, validated scalar values including captured nulls, source,
and selected policy names when known.

| Configuration field | Meaning |
| --- | --- |
| `configured_retry_type` | Selector before checking for a usable minimum |
| `delayed_retry_type`, `delayed_retry_min`, `delayed_retry_max` | Effective selector and millisecond bounds |
| `delivery_limit` | Counted-delivery bound; a negative value means unlimited |
| `delivery_count_basis` | `acquired` on 4.2, `failed` on 4.3 |
| `consumer_timeout` | Queue-level acknowledgement timeout in milliseconds |
| `consumer_disconnected_timeout` | Milliseconds before returning deliveries held by an unreachable consumer node |
| `dead_letter_exchange` | Resolved DLX name, absence, or uncertainty |
| `dead_letter_strategy`, `overflow` | Requested strategy and effective overflow |
| `effective_dead_letter_strategy` | `disabled`, `at-most-once`, `at-least-once`, or unknown |

Resolution supports captured **RabbitMQ 4.2 and 4.3** versions. On 4.2, the
audit marks delayed retry and 4.3 queue-owned timeouts `not_applicable`. The
legacy channel-level `consumer-timeout` setting remains available on 4.2.
Missing or unsupported versions retain captured inputs with unknown effective
values. Fixed defaults for supported versions, such as delivery limit 20,
carry `broker_default` evidence. Ordinary definitions exports omit the
broker-configurable global timeout defaults. Those defaults remain unknown when
no argument or policy supplies a value. This audit covers queue-level settings.

Policy patterns are not evaluated with Python regular expressions. Complete
queue/exchange observations supply selections and the broker's
`effective_policy_definition`. Possibly applicable policies without selection
evidence make affected settings unknown; they do not block graph construction
unless an existing routing-evidence requirement applies. Contradictory effective
policy evidence remains unknown. Invalid explicit selected-policy references
remain topology parsing errors.

To look up which captured resources selected a declaration, use the
[policy inventory](policy-inventory.md). It reads selection field presence
independently of setting resolution.

Resolution is per setting. For example, regular retry minimum `2000` and
operator minimum `3000` combine to `3000`; queue argument `1000` then resolves
to **1000 ms**. Operator retry type overrides the regular type; policy type
overrides the queue argument. Maximum bounds use minimum selection. Finite
delivery limits take precedence over negative unlimited values. See the pinned
[quorum resolver](https://github.com/rabbitmq/rabbitmq-server/blob/v4.3.6/deps/rabbit/src/rabbit_quorum_queue.erl),
[policy merging](https://github.com/rabbitmq/rabbitmq-server/blob/v4.3.6/deps/rabbit/src/rabbit_policies.erl),
and [timeout source](https://github.com/rabbitmq/rabbitmq-server/blob/v4.3.6/deps/rabbit/src/rabbit_queue_type.erl).

## Run an offline audit

Save a cluster-wide definitions export and complete queue/exchange JSON arrays
from one broker/window under the filenames below. Retain the broker version in
definitions. Sequential HTTP calls are not an atomic snapshot; the SDK cannot
prove capture completeness or temporal consistency.

```python
from pathlib import Path

from rabbitmq_management_sdk import ClusterAuditor
from rabbitmq_management_sdk.topology import ConfigurationStatus

auditor = ClusterAuditor.from_files(
    Path("full-export-definitions.json"),
    queues_path=Path("queues.json"),
    exchanges_path=Path("exchanges.json"),
)
report = auditor.audit(max_cycles=1000)
for result in report.quorum_delivery_configuration:
    config = result.configuration
    print(result.queue.vhost, result.queue.name, result.issues)
    print("Retry:", config.delayed_retry_type.status, config.delayed_retry_type.value)
    print("Delivery limit:", config.delivery_limit.value, config.delivery_count_basis.value)
    timeout = config.consumer_timeout
    if timeout.status == ConfigurationStatus.KNOWN:
        print("Queue timeout (ms):", timeout.value)
    else:
        print("Queue timeout unresolved:", timeout.uncertainties)
    for evidence in config.delayed_retry_min.evidence:
        print(evidence.source, evidence.key, evidence.value, evidence.policy_name)

for cycle in report.message_loop_candidates.cycles:
    print("Possible message loop:", cycle)
```

## Advisory facts

Findings carry no severity or environment-specific thresholds:

- `retry_disabled_without_minimum`: an enabled selector resolves to disabled
  retry because there is no positive minimum;
- `at_least_once_fallback`: requested at-least-once dead lettering resolves to
  at-most-once because effective overflow is incompatible;
- `finite_delivery_limit_without_dlx`: a finite limit has no configured DLX,
  so exceeding counted failures can discard messages.

A policy's `reject-publish-dlx` overflow falls back to `drop-head` for quorum
queues. The audit retains that input and reports the resulting strategy.
The [queue limits audit](queue-limits.md) uses this same overflow value.
Missing DLX resources and uncertain destinations remain part of
[DLX coverage](dead-letter-coverage.md). Configuration does not establish
consumer activity, delivery safety, or cycle termination.

## Verification map

Public value invariants live in `tests/unit/test_models.py`. Resolver/parser
tests live in `tests/unit/test_quorum_configuration.py` and
`tests/unit/test_parser_topology.py`. They verify metadata retention, unchanged
cycle paths, canonical ordering, and graph transfer.

The [sanitized 4.3.6 bundle](../tests/integration/rmq-4.3/quorum-configuration/README.md)
contains selected/effective policy observations and argument/operator conflicts.
`tests/integration/test_quorum_configuration_captures.py` replays those files
through the auditor. `tests/live/test_quorum_audit.py` runs on 4.2 and 4.3;
`tests/live/test_quorum_configuration.py` gates retry/timeout cases to 4.3.
