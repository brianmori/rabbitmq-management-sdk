# Audit dynamic shovel acknowledgement and transport

A shovel can acknowledge source deliveries after destination confirmation
and use TLS on only one leg. Check those settings independently. Call
`auditor.shovel_safety()` or read `report.shovel_safety` from `audit()`.
Each immutable `ShovelSafety` row is keyed by the shovel's full `NodeId`, with
rows in canonical identity order. The same evidence is available through
`from_topology()`.

## Source acknowledgement

`acknowledgement` gives the mode, its source, and the configured timing.

| Mode | What the mode says about source acknowledgement |
| --- | --- |
| `on-confirm` | Waits for positive destination confirmation or acceptance. |
| `on-publish` | Acknowledges after publishing at the destination without waiting for confirmation. |
| `no-ack` | Uses automatic acknowledgement or settled delivery at the source. |
| `unknown` | The available evidence does not establish a mode. |

A recognized captured `ack-mode` has source `explicit`. When the key is
absent, RabbitMQ 4.2 and 4.3 default to `on-confirm` for the built-in dynamic
shovel protocols `amqp091`, `amqp10`, and `local`. The audit labels that
inference `broker_default` when the capture supplies a supported version
and both protocols are recognized. An omitted protocol uses `amqp091`.
An explicit null or invalid value yields mode `unknown` and source
`invalid_value`. Missing version or protocol support leaves an omitted
setting at `unknown`. [Dynamic acknowledgement settings](https://www.rabbitmq.com/docs/4.2/shovel-dynamic)
and the pinned [4.2.9 parser](https://github.com/rabbitmq/rabbitmq-server/blob/v4.2.9/deps/rabbitmq_shovel/src/rabbit_shovel_parameters.erl#L174-L183)
and [4.3.6 parser](https://github.com/rabbitmq/rabbitmq-server/blob/v4.3.6/deps/rabbitmq_shovel/src/rabbit_shovel_parameters.erl#L215-L224).

Confirmation-based acknowledgement waits for more evidence from the
destination than the other modes. Recovery can still cause redelivery or
duplicate publication. Exactly-once delivery requires guarantees outside
this setting.

## Transport on each leg

`source_transport` and `destination_transport` each have a summary
`kind` and a tuple of `candidates`, in supplied failover order, including
duplicates. Candidate classifications contain only enum values.

| Transport | Captured evidence |
| --- | --- |
| `local` | The local shovel protocol or a verified hostless AMQP 0-9-1 direct connection. |
| `network_amqp` | A supported hosted AMQP URI with scheme `amqp`, configured without TLS. |
| `network_amqps` | A supported hosted AMQP URI with scheme `amqps`, configured for TLS. |
| `mixed` | At least two distinct known transport kinds on the endpoint. |
| `unknown` | Missing or unsupported endpoint evidence, or unknown candidates that prevent a single known summary. |

For a recognized `local` leg, the broker uses URI information to select a
user and vhost and accesses broker resources directly. For AMQP 0-9-1,
hostless `amqp` without an explicit port uses the broker's direct client on
4.2 and 4.3. Hostless `amqps` and AMQP 0-9-1 hostless URIs with explicit
ports cannot establish a valid transport. AMQP 1.0 uses a network client,
and the audit leaves hostless AMQP 1.0 unknown. This classification uses
the supported URI forms of the SDK. [4.2.9 AMQP URI handling](https://github.com/rabbitmq/rabbitmq-server/blob/v4.2.9/deps/amqp_client/src/amqp_uri.erl#L107-L185),
[4.3.6 AMQP URI handling](https://github.com/rabbitmq/rabbitmq-server/blob/v4.3.6/deps/amqp_client/src/amqp_uri.erl#L117-L198),
[4.2.9 AMQP 1.0 client](https://github.com/rabbitmq/rabbitmq-server/blob/v4.2.9/deps/amqp10_client/src/amqp10_client.erl#L434-L481),
[4.3.6 AMQP 1.0 client](https://github.com/rabbitmq/rabbitmq-server/blob/v4.3.6/deps/amqp10_client/src/amqp10_client.erl#L434-L481),
and the [local shovel implementation](https://github.com/rabbitmq/rabbitmq-server/blob/v4.3.6/deps/rabbitmq_shovel/src/rabbit_local_shovel.erl#L255-L291).

A malformed failover entry has an `unknown` slot and leaves other entries
visible. The endpoint can still establish `mixed` when two known kinds
differ, even with unknown slots. An absent candidate list has an empty tuple
and an `unknown` summary. A result describes every supplied candidate and
leaves selection and connection activity unresolved.

Graph locality requires evidence for every failover candidate. A hosted
network leg uses the operator's `in_cluster_amqp_hosts` mapping. A valid
hostless direct AMQP 0-9-1 URI can establish locality even when the capture
lacks a supported broker version for transport classification. Hostless
`amqps`, an explicit port without a host, and hostless AMQP 1.0 leave the
endpoint unconfirmed even when its vhost or authority is known. See
[shovel connection and vhost evidence](topology-graph-construction.md#connection-and-vhost-evidence).

`network_amqps` establishes TLS configuration only. Peer verification,
negotiation success, and transfers require runtime evidence. The safety
metadata contains no raw URI, userinfo, query options, certificate paths,
or arbitrary shovel parameters. Static shovels absent from definitions are
outside the audit's scope.

## Try the sanitized capture

Run from the repository root. These are projections of existing disposable
broker scenarios. The loader supplies the captured version alongside the
projected dynamic parameters.

```python
import json
from pathlib import Path

from rabbitmq_management_sdk import ClusterAuditor
from rabbitmq_management_sdk.resources.v4.admin.schemas.export_response import ClusterDefinitionsResponse

capture = Path("tests/integration/rmq-4.3/shovel-direction-evidence.json")
evidence = json.loads(capture.read_text(encoding="utf-8"))
wanted = {"local-source-omitted", "amqp10-destination-v2-omitted"}
definitions = ClusterDefinitionsResponse.model_validate(
    {
        "rabbitmq_version": evidence["broker_version"],
        "parameters": [
            {"component": "shovel", "vhost": "captured", "name": case["name"], "value": case["value"]}
            for case in evidence["cases"]
            if case["name"] in wanted
        ],
    }
)
auditor = ClusterAuditor(definitions, in_cluster_amqp_hosts={"localhost"})
for row in auditor.shovel_safety():
    print(
        row.shovel.name,
        row.acknowledgement.mode,
        row.acknowledgement.source,
        row.source_transport.kind,
        row.destination_transport.kind,
    )
```

```text
amqp10-destination-v2-omitted on-confirm broker_default network_amqp network_amqp
local-source-omitted on-confirm broker_default local local
```

The capture projections and their verification are documented in the
[graph construction guide](topology-graph-construction.md#implementation-and-test-map).
