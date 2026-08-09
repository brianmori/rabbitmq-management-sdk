# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-08-08

### Added
- Dynamic shovel management through `RabbitMQClient.shovels`, including typed
  AMQP 0-9-1, AMQP 1.0, and local source/destination models and shovel status
  queries.
- Typed cluster-wide and virtual-host definitions exports through
  `AdminManager.export_definitions()` and `AdminManager.export_vhost_definitions()`.
- `ClusterAuditor` and immutable topology graph models for constructing and
  auditing captured RabbitMQ configuration. Analysis covers strongly connected
  components, structural cycles, message-loop candidates, dangling edges,
  reachability findings, and shovel endpoint/vhost findings.
- Policy-aware topology construction using broker-observed queue and exchange
  selections for dead-letter and alternate-exchange routes, without locally
  approximating RabbitMQ policy regular expressions.
- Paginated queue and exchange listing with page metadata, name filtering,
  regular-expression filtering, optional statistics suppression, and
  deterministic `list_all()`/`list_by_vhost()` conveniences.
- Typed regular and operator policy management through
  `RabbitMQClient.policies` and `RabbitMQClient.operator_policies`, including
  create/update, get, list, and delete operations. Known definition settings
  are typed while plugin-provided keys are preserved during validation and
  wire serialization.
- Pickle-compatible serialization for `ClusterTopology`. Derived lookup caches are
  rebuilt on deserialization so topology values can be transferred across
  spawned process boundaries and analyzed with `ClusterAuditor.from_topology()`.
- Top-level re-exports of request/response models and enums, so the public API
  can be imported directly from the package root (e.g.
  `from rabbitmq_management_sdk import QueueRequest`).
- `RabbitMQClient.version` property exposing the broker version detected at construction.
- A topology-specific `RabbitMQError` hierarchy covering loading, definitions
  validation, resource snapshots, graph translation, domain validation, and
  analysis requests.

### Changed
- Lowered the minimum supported Python version from 3.14 to **3.12**.
- Moved resource implementations from `rabbitmq_management_sdk.domains` to
  `rabbitmq_management_sdk.resources`.
- Removed redundant `V4` suffixes from manager class names; API-version routing
  remains the responsibility of `RabbitMQClient`.
- `admin.get_vhost_limits()` now returns `VhostLimitResponse | None`
  (previously `VhostLimitResponse`) and returns `None` when a vhost has no
  configured limits.

## [0.1.0] - 2026-06-07

### Added
- Initial release: a typed client for the RabbitMQ HTTP Management API covering
  queues, exchanges, bindings, virtual hosts, and limits—with automatic
  broker-version detection, Basic-Auth and mTLS support, an exponential-backoff
  retry transport, and a `RabbitMQError` exception hierarchy.

[Unreleased]: https://github.com/brianmori/rabbitmq-management-sdk/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/brianmori/rabbitmq-management-sdk/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/brianmori/rabbitmq-management-sdk/releases/tag/v0.1.0
