# Sanitized vhost permissions and limits captures

These are offline replay tests. The [current live test suite](../../README.md)
uses **RabbitMQ 4.2.9 and 4.3.8**.

## Saved fixture provenance

The fixtures are projections from existing cluster-wide Management API
definitions captures. They retain version, vhost names, ordinary and topic
permission rows, and vhost limit fields for the selected vhosts.

| Fixture and source broker | Original capture | Evidence |
| --- | --- | --- |
| [4.2.8](rmq-4.2.8.json) | [4.2 full export](../rmq-4.2/full-export-definitions.json) | Two integer caps on one vhost, only a connection cap on another, and topic declarations including an empty exchange string |
| [4.3.6](rmq-4.3.6.json) | [4.3 complex topology](../rmq-4.3/complex-topology/README.md) | An empty embedded vhost limit object and an explicitly empty topic permission section |

Vhosts and principals are renamed consistently within each projection.
Patterns, exchange names and limit counts keep their captured values.
User records, password hashes, cluster metadata, routing resources and
unrelated parameters are excluded. Replay measures decoding of these
definitions. It performs no broker requests or authorization checks.
