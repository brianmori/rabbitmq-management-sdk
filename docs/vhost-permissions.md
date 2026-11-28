# Vhost permissions and limits inventory

Use `ClusterAuditor.vhost_permissions_inventory()` on RabbitMQ 4.2 or 4.3
cluster definitions to see which ordinary and topic permissions were captured
for each vhost and its configured queue and connection caps. The same inventory
is available on `auditor.audit().vhost_permissions_inventory`.

## Read the declarations for one vhost

Run this example with the SDK installed. It uses a small in-memory capture, so
it needs no broker. To read a saved cluster export, construct the auditor with
`ClusterAuditor.from_files(path)`.

```python
from rabbitmq_management_sdk import ClusterAuditor
from rabbitmq_management_sdk.resources.v4.admin.schemas.export_response import (
    ClusterDefinitionsResponse,
)

definitions = ClusterDefinitionsResponse.model_validate({
    "rabbitmq_version": "4.3.6",
    "vhosts": [{"name": "/payments"}],
    "permissions": [{
        "vhost": "/payments",
        "user": "payments-api",
        "configure": "^$",
        "write": r"^payments\.in$",
        "read": "^$",
    }],
    "topic_permissions": [{
        "vhost": "/payments",
        "user": "payments-api",
        "exchange": "payments.in",
        "write": r"^payments\.created$",
        "read": "^$",
    }],
    "parameters": [{
        "vhost": "/payments",
        "component": "vhost-limits",
        "name": "limits",
        "value": {"max-queues": 200, "max-connections": 80},
    }],
})

inventory = ClusterAuditor(definitions).vhost_permissions_inventory()
payments = next(row for row in inventory.vhosts if row.vhost == "/payments")
for grant in payments.permissions or ():
    print("Ordinary", grant.principal, grant.write_pattern)
for grant in payments.topic_permissions or ():
    print("Topic", grant.principal, grant.exchange, grant.write_pattern)
print("Queues", payments.max_queues.status, payments.max_queues.value)
print("Connections", payments.max_connections.status, payments.max_connections.value)
```

```text
Ordinary payments-api ^payments\.in$
Topic payments-api payments.in ^payments\.created$
Queues capped 200
Connections capped 80
```

`VhostPermissionsInventory.vhosts` is ordered by exact vhost name and
includes declared vhosts with no routing resources. Ordinary grants are
ordered by principal, configure, write, and read patterns. Topic grants
are ordered by principal, exchange, write and read patterns. Principals
with the same name in different vhosts stay in separate rows. Patterns
are preserved as text. The inventory describes captured declarations.
Effective authorization and organizational policy are outside its scope.

## Interpret the capture evidence

Each permissions section has independent presence. A vhost row has
`permissions=None` or `topic_permissions=None` when that section was
omitted, and an empty tuple when the section was supplied with no entries
for that vhost. The inventory's `permissions_captured` and
`topic_permissions_captured` flags also preserve this difference when
there are no vhost rows. [Capture contract](capture-contract.md).

The caps are counts of queues and streams or concurrent client connections
within a vhost. The matching cluster definitions parameter has
`component="vhost-limits"` and `name="limits"`. RabbitMQ reads this parameter
when enforcing the caps. If the capture has no matching parameter, the
inventory can use the `limits` object embedded in a cluster vhost record.
The normalized row retains only the two known limit keys' meanings and
integer values. [RabbitMQ 4.2 vhost limits](https://www.rabbitmq.com/docs/4.2/vhosts#limits),
[4.2.9 broker lookup](https://github.com/rabbitmq/rabbitmq-server/blob/v4.2.9/deps/rabbit/src/rabbit_vhost_limit.erl#L173-L186),
[4.3.6 broker lookup](https://github.com/rabbitmq/rabbitmq-server/blob/v4.3.6/deps/rabbit/src/rabbit_vhost_limit.erl#L173-L186).

| Captured evidence | Status | Retained value and meaning |
| --- | --- | --- |
| Positive integer | `capped` | The captured count is the configured maximum. |
| Zero | `blocked` | The captured value is `0`. The vhost cap blocks new queue declarations or client connections, depending on the setting. |
| Negative integer | `unlimited` | The original negative value is preserved. RabbitMQ interprets it as no cap for this setting. |
| Matching runtime parameter with an empty list or object | `cleared` | Both limits have no value in this explicit empty definition. |
| A key absent from a nonempty limit object, an empty embedded object, or a supplied parameters section with no matching record or embedded object | `unset` | No value for that cap was declared in the available evidence. |
| Missing parameters and embedded evidence, malformed limit values, or conflicting matching records for a key | `unavailable` | A count for this setting cannot be established. |

The status concerns the captured per-vhost cap. Other broker limits,
current usage, and later changes require separate evidence. The
[`queue_limits()` audit](queue-limits.md) addresses individual queue settings.

Trusted graph transfer preserves the projected inventory for
`ClusterAuditor.from_topology()`. A hand-built graph without permission or
limit metadata has unavailable evidence for its known vhosts. User records,
hashes, and unrelated parameter data are outside the retained inventory.
[Sanitized capture fixtures](../tests/integration/vhost-permissions/README.md).
