# Reverse policy selection lookup

`ClusterAuditor.policy_lookup()` answers which captured queues or exchanges
the broker reported as selecting a regular or operator policy. It works
offline with RabbitMQ 4.2 and 4.3 captures. `policy_inventory()` and
`audit().policy_inventory` expose the same declarations and the selection
evidence for individual resources.

## Look up a declared policy

Run this example with the SDK installed. It uses in-memory definitions and
observations. For saved files, use
`ClusterAuditor.from_files(definitions_path, queues_path=queues_path, exchanges_path=exchanges_path)`.

```python
from rabbitmq_management_sdk import ClusterAuditor
from rabbitmq_management_sdk.resources.v4.admin.schemas.export_response import (
    ClusterDefinitionsResponse,
)
from rabbitmq_management_sdk.resources.v4.queues.schemas.queue_response import QueueResponse
from rabbitmq_management_sdk.topology import PolicyId, PolicyKind

vhost = "/payments"
definitions = ClusterDefinitionsResponse.model_validate({
    "rabbitmq_version": "4.3.6",
    "vhosts": [{"name": vhost, "metadata": {"default_queue_type": "classic"}}],
    "queues": [
        {"name": name, "vhost": vhost, "durable": True, "auto_delete": False}
        for name in ("a", "b", "c")
    ],
    "policies": [{
        "name": "retention",
        "vhost": vhost,
        "pattern": r"^[abc]$",
        "apply-to": "queues",
        "priority": 10,
        "definition": {"message-ttl": 60000},
    }],
})
observations = [
    QueueResponse.model_validate({
        "name": name,
        "vhost": vhost,
        "type": "classic",
        "durable": True,
        "auto_delete": False,
        "arguments": {"x-queue-type": "classic"},
        "node": "rabbit@captured",
        "state": "running",
        **fields,
    })
    for name, fields in (
        ("a", {"policy": "retention"}),
        ("b", {"policy": None}),
        ("c", {}),
    )
]
auditor = ClusterAuditor(definitions, queues=observations, exchanges=[])
key = PolicyId(vhost, "retention", PolicyKind.REGULAR, auditor.topology.cluster_id)
match = auditor.policy_lookup(key)
assert match is not None
print("Selected resources", [resource.name for resource in match.selected_resources])
for selection in auditor.policy_inventory().selections:
    if selection.resource.vhost == vhost and selection.kind is key.kind:
        print(selection.resource.name, selection.status)
```

```text
Selected resources ['a']
a selected
b no_selection
c not_captured
```

`PolicyId` uses the captured cluster ID, exact vhost, kind and name. A
regular and operator policy can share a name, and one resource can select
both. Older captures can have `cluster_id=None`. Use the ID from the
audited topology rather than its display label. The lookup returns `None`
when the key has no unique readable declaration.

## Interpret the inventory

`PolicyCoverage.declaration` retains the policy's pattern, `apply_to` and
priority. Regular declarations come from `policies`. Operator declarations
come from the runtime parameters with `component="operator_policy"`. Each
`selected_resources` tuple is ordered by complete resource identity. It
links named observations to a uniquely captured declaration with a compatible
`apply_to` scope. If the declaration is missing, ambiguous, unreadable, or
outside that scope, the reported name can still be available in the resource
selection row. Existing routing and queue configuration validation continues
to apply when the topology is constructed.
[RabbitMQ policy guide](https://www.rabbitmq.com/docs/4.2/policies),
[4.2.9 broker policy declarations](https://github.com/rabbitmq/rabbitmq-server/blob/v4.2.9/deps/rabbit/src/rabbit_policy.erl#L49-L63).

`PolicyInventory.selections` contains one row per resource and policy kind,
ordered by resource identity with regular before operator. It includes
queues and exchanges contained in the graph and resources found only in the
observations. `missing_selections` returns the subset with unavailable
evidence. Purely dangling identities have no selection rows. A nonempty
captured name yields `selected`, explicit JSON null yields `no_selection`,
and a missing observation or unusable field yields `not_captured`. Standard
exchange information in 4.2 and 4.3 omits the operator policy field.
[4.2.9 exchange information](https://github.com/rabbitmq/rabbitmq-server/blob/v4.2.9/deps/rabbit/src/rabbit_exchange.erl#L30-L34),
[4.3.6 exchange information](https://github.com/rabbitmq/rabbitmq-server/blob/v4.3.6/deps/rabbit/src/rabbit_exchange.erl#L30-L34).

An empty `selected_resources` tuple means the capture established no
selection for that declaration. It is insufficient to conclude a policy is
unused. The inventory relies on the named selection field alone. Patterns,
priorities and effective settings carry no evidence of which name RabbitMQ
selected. Supply complete paired resource observations from the same broker
and capture window. Preserve field presence when saving them.
[Capture contract](capture-contract.md).

`ClusterAuditor.from_topology()` works with the retained evidence after
trusted graph transfer. The inventory retains declaration headers and
selection facts. Effective policy settings belong to the separate
[quorum audit](quorum-queue-auditing.md) and [queue limits audit](queue-limits.md).
