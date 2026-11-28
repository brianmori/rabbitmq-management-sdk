# Compare two captures of one cluster

Suppose the direct exchange `events` in vhost `orders` routes the empty
routing key to `worker`. After a deployment removes that binding, a
comparison can show the exact binding that disappeared and whether the
publication still satisfies your routing contract.

## Compare saved captures and a routing contract

Install the SDK and place two bundles from the same RabbitMQ cluster at
`before.json` and `after.json`. Collect and save each bundle using the
[capture workflow](capture-contract.md#collect-save-and-replay-an-audit-capture).
For this example, the starting capture has an `events` → `worker` binding
with key `""` and arguments `{}`. In the later capture, the exchange and
queue still exist, the binding is absent, and no other route handles the key.

```python
from pathlib import Path

from rabbitmq_management_sdk import AuditCapture, compare_captures
from rabbitmq_management_sdk.topology import NodeId, NodeKind, RoutingCondition, RoutingContract

before = AuditCapture.load(Path("before.json"))
after = AuditCapture.load(Path("after.json"))

origin = NodeId("orders", "events", NodeKind.EXCHANGE)
worker = NodeId("orders", "worker", NodeKind.QUEUE)
contract = RoutingContract(
    "Empty key reaches worker",
    origin,
    "",
    (RoutingCondition.reaches_all({worker}),),
)
comparison = compare_captures(before, after, routing_contracts=[contract])

for binding in comparison.bindings.removed:
    print(binding.source.name, "->", binding.target.name, repr(binding.routing_key))

for outcome in comparison.routing_contracts:
    print(outcome.contract.name, outcome.before.verdict, "->", outcome.after.verdict)
    if outcome.before.gaps or outcome.after.gaps:
        print("Capture gaps:", outcome.before.gaps, outcome.after.gaps)
```

```text
events -> worker ''
Empty key reaches worker satisfied -> violated
```

You can pass auditors created with `ClusterAuditor.from_files()` or with
validated in-memory responses. `before_auditor.compare(after_auditor, ...)`
is the same operation with the auditor as the baseline. Auditors and bundles
can be mixed. The comparison reads the evidence already held by the inputs.

## Read the comparison

| Result field | Evidence and order |
| --- | --- |
| `vhosts`, `queues`, `exchanges`, `shovels`, `regular_policies`, `operator_policies` | Added and removed identities from captured definitions. The `attributes` rows name changed or unavailable fields for identities present on both sides. Queue and exchange shapes use captured declaration arguments and flags. Vhosts compare their default queue type, regular policies compare their selector and definition fields, and operator policies compare their captured configuration. Shovels report endpoint and acknowledgement changes under `value` and name the supported transfer settings that changed. A shovel URI comparison uses its credential-free capture form. |
| `bindings` | Added and removed binding edges in canonical edge order. Endpoints, destination kind, routing key, and the complete argument map form the identity. Parallel bindings stay distinct. An argument change removes one binding identity and adds another. |
| `policy_selections` | Observed regular and operator selection changes for queues and exchanges represented in both inventories. Explicit null records no selection. A missing selector goes into `unavailable`. |
| `routing_contracts` | Before and after outcomes in the order you supplied the contracts. Each evaluated outcome contains the usual routing result with condition verdicts, destinations, and routing issues. An outcome blocked by capture evidence reports `undetermined` with its `gaps`. |

Identity uses the broker's `internal_cluster_id`. A captured name, label, or
host does not establish identity. If either input lacks an ID, pass
`cluster_id="your-explicit-mapping"` to affirm that both came from the same
cluster. Use the captured ID as the mapping when one side already has it.
Conflicting captured IDs raise `TopologyAnalysisError`, even with a mapping.
Contract resource IDs with an omitted cluster ID use the comparison's
cluster ID. The comparison also uses that ID for reported declarations,
bindings, policy selections, and routing destinations.

Pass `before_vhosts` and `after_vhosts` for manually assembled projections
of cluster-wide responses. The comparison reports identity changes inside
their intersection. If the scopes differ, affected categories are
indeterminate outside that intersection. A contract can still be evaluated
on a side that covers its origin vhost. A vhost-specific Management API
definitions export has a different schema.

Read a category's `indeterminate` flag alongside its changes. If either
definitions section is omitted, an empty change list says nothing about
additions or removals in that category. An explicit empty section can
establish removals within the shared scope. A missing or redacted argument
map prevents comparison of bindings sharing its endpoints and routing key.
An omitted bindings section leaves publication through ordinary exchanges
undetermined. A headers binding with unavailable arguments leaves its
matching decision uncertain. Default-exchange contracts use implicit queue routes.

Reported declaration attributes describe the retained wire fields. An
explicit null differs from a missing field, and capture-local redaction
tokens cannot establish equality across captures. A redacted shovel transfer
setting is indeterminate. Supplemented exchanges and runtime statistics provide
other audit evidence and do not count as declarations. A topology-only auditor
can still supply retained policy observations. Its definition-based categories
and capture-backed routing outcomes are indeterminate because it lacks the
original section presence.

The comparison cannot establish cluster visibility or atomicity for
sequential reads. Record account visibility, collection scope, and timing
when interpreting a change. [Capture and evidence contract](capture-contract.md)
