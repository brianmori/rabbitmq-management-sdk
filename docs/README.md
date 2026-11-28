# SDK documentation

For installation and management API examples, see the [SDK README](../README.md).
To try topology auditing without a broker, [replay a saved capture](topology-auditing.md#audit-from-files)
or [build a four-resource example graph](topology-graph-construction.md#a-small-runnable-example).

## Topology auditing

- [Read an audit report](topology-auditing.md) and interpret structural cycles,
  possible message loops, and missing routes.
- [Trace configured paths](topology-tracing.md) upstream or downstream from a
  queue, exchange, or shovel.
- [Check exchange routing and publication contracts](exchange-routing-coverage.md)
  for a given routing key.
- [Check dead-letter exchange coverage](dead-letter-coverage.md) to see where a
  queue can republish rejected or expired messages.
- [Inspect shovel acknowledgements and transport](shovel-safety.md).
- [Read captured quorum delivery settings](quorum-queue-auditing.md), including
  retry, timeout, and delivery limits.
- [Evaluate quorum placement and failure scenarios](quorum-resilience.md).

## Configuration and inventory

- [Construct requests and parse responses](resource-models.md).
- [Configure quorum queues on RabbitMQ 4.3](quorum-queue-configuration.md).
- [Inspect queue limits and stream retention](queue-limits.md).
- [Inventory captured vhost permissions and limits](vhost-permissions.md).
- [Find resources that selected a policy](policy-inventory.md).
- [Review binding durability and RabbitMQ 4.3 declaration compatibility](declaration-compatibility.md).

## Capture and implementation reference

- [Collect and sanitize broker captures](capture-contract.md).
- [See how the SDK builds a topology graph](topology-graph-construction.md).
- [Read known broker limitations](known-broker-limitations.md).
- [Find implemented audit capabilities and their tests](topology-auditing.md#implemented-capabilities-and-evidence).
