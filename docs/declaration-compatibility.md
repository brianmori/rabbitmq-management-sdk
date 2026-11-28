# Binding durability and RabbitMQ 4.3 queue declarations

Use `ClusterAuditor.binding_durability()` to read the endpoint flags of
captured bindings. Use `ClusterAuditor.queue_declaration_compatibility()`
to check non-durable classic queue declarations against the RabbitMQ 4.3
default. Both work offline on 4.2 or 4.3 captures, including through
`ClusterAuditor.from_topology()`. The aggregate `audit()` report includes both.

## Read the findings together

Run this example from the repository root with the SDK installed. The
sanitized RabbitMQ 4.2.9 capture contains a non-durable classic queue called
`shared` and a binding from a durable exchange to that queue.

```python
import json
from pathlib import Path

from rabbitmq_management_sdk import ClusterAuditor
from rabbitmq_management_sdk.resources.v4.admin.schemas.export_response import ClusterDefinitionsResponse
from rabbitmq_management_sdk.resources.v4.exchanges.schemas.exchange_response import ExchangeResponse
from rabbitmq_management_sdk.resources.v4.queues.schemas.queue_response import QueueResponse

path = Path("tests/integration/declaration-compatibility/rmq-4.2.9.json")
capture = json.loads(path.read_text(encoding="utf-8"))
auditor = ClusterAuditor(
    ClusterDefinitionsResponse.model_validate(capture["definitions"]),
    queues=[QueueResponse.model_validate(entry) for entry in capture["queues"]],
    exchanges=[ExchangeResponse.model_validate(entry) for entry in capture["exchanges"]],
)

for binding in auditor.binding_durability():
    if (binding.source.name, binding.target.name) == ("source.durable", "shared"):
        print("Binding", binding.source_durable, binding.target_durable, binding.classification)

for queue in auditor.queue_declaration_compatibility():
    print("Queue", queue.queue.name, queue.target_version, queue.assumes_default_settings, queue.status)
```

```text
Binding True False semi_durable
Queue shared 4.3 True rejected_by_default
```

Both rows describe the same captured queue. The binding row describes the
endpoints. The queue row predicts the outcome of submitting that queue
declaration under the specific target default.

## Binding durability

Each `BindingDurability` retains its original `binding` edge, including the
routing key and arguments. Its `source` and `target` are complete `NodeId`
values. Queue and exchange destinations use the same endpoint categories.
Parallel bindings remain separate. The order follows `edge_sort_key`.

| Source durable | Target durable | Classification |
| --- | --- | --- |
| `True` | `True` | `fully_durable` |
| `True` | `False` | `semi_durable` |
| `False` | `False` | `fully_transient` |
| `False` | `True` | `unclassified` |
| Either flag is `None` | | `unknown` |

The categories follow the versioned exchange guides. The guides leave the
transient-source, durable-target combination unnamed, so the SDK returns
both flags with `unclassified`. The 4.2 legacy Mnesia route-table helper
uses a different mapping for mixed endpoint flags. A capture records endpoint
configuration and cannot establish route-table placement or restart behavior.
[RabbitMQ 4.2 exchanges](https://www.rabbitmq.com/docs/4.2/exchanges#binding-durability),
[RabbitMQ 4.3 exchanges](https://blog.rabbitmq.com/docs/4.3/exchanges#binding-durability),
[4.2.9 route-table helper](https://github.com/rabbitmq/rabbitmq-server/blob/v4.2.9/deps/rabbit/src/rabbit_binding.erl#L160-L168).

Only captured binding edges get a row. Dead-letter, alternate and shovel hops,
and the implicit default-exchange routes are outside this finding.

## The RabbitMQ 4.3 default queue declaration rule

Each `QueueDeclarationCompatibility` row represents a declared classic queue
with `durable=False`. Durable queues, quorum queues, streams and other queue
types are outside the result. Rows are sorted by full queue identity.

The target is always RabbitMQ `4.3` with default settings for
`deprecated_features.permit.transient_nonexcl_queues`. The input broker
version describes the capture. A target deployment can explicitly permit
these declarations, and that override may be absent from captured evidence.
[RabbitMQ 4.3.0 release notes](https://github.com/rabbitmq/rabbitmq-server/blob/v4.3.0/release-notes/4.3.0.md#deprecated-features-are-now-disabled-by-default).

| Observed `exclusive` | Status under the target default |
| --- | --- |
| `False` | `rejected_by_default` |
| `True` | `not_affected` by this rule |
| `None` | `indeterminate` |

The parser keeps exclusivity from a matching queue observation when its type
and durable flag agree with the declaration. A missing observation or an
omitted or null flag leaves exclusivity unknown. Queue names, `auto_delete`
and definitions alone cannot establish it. Exclusive queues are omitted by
the broker's definitions export, so observation-only queues fall outside this
declared-queue report. The capture can omit temporary queues. [Capture contract](capture-contract.md),
[RabbitMQ 4.3 definitions export](https://github.com/rabbitmq/rabbitmq-server/blob/v4.3.6/deps/rabbit/src/rabbit_definitions.erl#L303-L320).

The status addresses a declaration request under one default setting. It
does not assess whether a captured queue is currently operating or whether
any other declaration constraint would apply.

## Disposable broker evidence

RabbitMQ 4.2.9 and 4.3.8 both accepted an exclusive non-durable classic
queue and omitted it from definitions while its connection was open. 4.2.9
accepted a non-exclusive non-durable classic declaration. 4.3.8 rejected
that declaration under the default rule. Both accepted exchange-to-exchange
bindings for all four endpoint durability combinations, including two
transient exchanges, and bindings from either source durability to a durable
queue. These observations cover creation and capture. [Sanitized captures and reproduction](../tests/integration/declaration-compatibility/README.md).
