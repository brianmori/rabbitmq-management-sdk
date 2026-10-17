# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.0] - 2026-10-17

### Added
- `ClusterAuditor.competing_shovel_consumers()` identifies two or more shovels
  configured to consume from the same confirmed-local queue. Results preserve
  cluster and virtual-host identity, exclude known stream queues, and describe
  potential contention rather than active consumers or proven message loss.
  Immutable `CompetingShovelConsumers` results are also included in `audit()`.
- `ClusterAuditor.fanout_exchange_bindings()` returns each fanout exchange and
  its distinct, directly bound queues as immutable `FanoutExchangeBindings`
  and `FanoutQueue` values, also included in `audit()`. Results keep virtual
  hosts separate, deduplicate parallel bindings, and retain dangling queue
  identities. Quorum replication factors use captured queue membership,
  including the leader, and remain `None` when unavailable. Membership counts
  are retained on `QueueNode.replication_factor` for offline graph analysis;
  they are not physical disk-write estimates.

### Changed
- Replaced HTTPX with HTTPX2 as the HTTP backend. The concrete adapter is now
  `http_adapter.httpx2.Httpx2Adapter`, and custom transports use HTTPX2 types
  such as `httpx2.MockTransport`. HTTP client loggers are now named `httpx2` and
  `httpcore2.*`, and the default User-Agent is `python-httpx2/<version>`.
  Explicit SDK CA and mutual TLS configuration continues to use the supplied
  SSL context.

### Fixed
- TLS certificate-loading failures during client initialization raise
  `TransportError`, preserving the underlying SSL or file I/O exception.
- Shovel response serialization round trips preserve nested endpoints and their
  protocols with either alias setting.
- Quorum queue requests and responses use `x-quorum-initial-group-size` for
  `initial_cluster_size`, preserving the requested initial replication size.
- Cluster definitions parsing rejects error responses, empty objects,
  single-vhost exports, and incompatible export formats instead of treating
  them as empty cluster captures. Older exports without format metadata and
  valid empty definitions sections remain accepted.

## [0.3.0] - 2026-10-14

### Added
- Public `RabbitMQClient.close()` and context-manager support for deterministic
  connection cleanup, including cleanup after failed version discovery.

### Changed
- Binding creation returns raw virtual-host names and `properties_key` values,
  matching binding-list responses. Pass names and binding keys without URL
  pre-encoding; managers encode individual path segments.
- `HttpResponse.json()` accurately exposes an arbitrary decoded JSON value as
  `object`, rather than incorrectly annotating every response as a dictionary.
- Updated locked runtime and development dependencies to address reported
  advisories in AnyIO, IDNA, msgpack, pip, and urllib3.

### Fixed
- Definitions exports accept the `all-with-x` and `any-with-x` headers binding
  modes, preserving their arguments and parallel topology edges.
- Definitions parameters preserve component-specific JSON values, including
  cleared vhost limits and array-valued federation upstream sets.
- Queue declarations retain `x-queue-type` inside `arguments` in compatibility
  mode, preventing the virtual host default from silently changing the requested
  classic, quorum, or stream implementation.
- Exchange declarations send `auto_delete` using the broker's correct wire key;
  the former `auto-delete` spelling remains accepted during model validation.
- Resource names, administrative vhost arguments, and binding keys are encoded
  consistently, including URL delimiters, literal percent signs, UTF-8 names,
  and dot-only path segments.
- Malformed list and overview response containers raise SDK errors instead of
  leaking Python exceptions or silently becoming empty resource lists.
- Captured-file loading maps JSON decoder value and recursion failures to
  `TopologyLoadError`, preserving the underlying cause.
- CI explicitly selects each job's Python version and verifies matrix
  interpreters before running tests.
- Built wheels and source distributions include the Apache-2.0 license text.
- Basic authentication supports UTF-8 usernames/passwords and sends credentials
  even when the configured password is empty.
- Operations on a closed HTTP adapter raise `TransportError`.
- Message-loop candidate searches skip SCCs without an internal republishing
  hop, avoiding unnecessary structural-cycle enumeration.
- Retry backoff waits only between attempts, without delaying the final failure.

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

[Unreleased]: https://github.com/brianmori/rabbitmq-management-sdk/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/brianmori/rabbitmq-management-sdk/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/brianmori/rabbitmq-management-sdk/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/brianmori/rabbitmq-management-sdk/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/brianmori/rabbitmq-management-sdk/releases/tag/v0.1.0
