# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Collect cluster-wide definitions and paired queue and exchange observations
  with `RabbitMQClient.collect_audit_capture()`. Save and replay a sanitized
  bundle with a versioned acquisition manifest.
- Compare same-cluster captures offline with `compare_captures()`, including
  declaration and binding changes, observed policy selections, and before and
  after routing contract outcomes.
- `ClusterAuditor.trace_topology()` follows captured edges upstream or
  downstream from a resource and reports path ends and depth cutoffs.
- List streams across virtual-host queue pages and update age and byte
  retention through a dedicated regular policy. See the
  [management HTTP example](docs/stream-lifecycle.md).

### Fixed

- Validate stream retention ages using RabbitMQ's case-sensitive units in
  declaration and policy requests. Use `7D` for seven days.

## [0.6.0] - 2026-11-26

### Added

- RabbitMQ 4.3 quorum retry and timeout settings in queue and policy requests,
  with an audit of effective quorum delivery configuration.
- Quorum resilience analysis for captured replica placement, broker
  concentration, and modeled node failures.
- Routing contracts for required, forbidden, and exclusive destinations, plus
  direct dependency reports for queues and exchanges.
- Queue limit, retention, and overflow audits. Shovel audits report
  acknowledgement mode and endpoint transport.
- AMQP 1.0 shovel source consumer names on RabbitMQ 4.3.
- Binding durability and queue declaration compatibility audits for RabbitMQ
  4.3 upgrades, plus inventories of vhost permissions, limits, and policies.
- A [RabbitMQ 4.3.8 extreme topology capture and benchmark](tests/integration/rmq-4.3/extreme-topology/benchmark.py)
  with 5,000 resource nodes, 4,986 edges, and expected audit results.

### Changed

- Moved the SDK's development status from Alpha to Beta.
- Documented RabbitMQ 4.2 and 4.3 as the supported broker series and pinned
  live tests to RabbitMQ 4.2.9 and 4.3.8. Saved captures retain their provenance.

### Fixed

- Queue, policy, operator-policy, and shovel writes reject known RabbitMQ
  4.3-only settings on older brokers with `UnsupportedBrokerFeatureError`
  before sending a request. The SDK uses the detected broker version or the
  configured version override.
- Routing contracts, exchange coverage, and dead-letter coverage preserve
  uncertainty when incomplete policy evidence or missing exchange arguments
  could hide an alternate route. Matching observations can recover an
  alternate argument omitted from a definitions export.
- Quorum delivery audits use captured queue observations when the definitions
  argument map is missing and report unknown settings when missing arguments
  or policy sections leave the default unconfirmed.
- Dead-letter routing uses captured classic and quorum queue arguments when the
  definitions export omits them. Coverage retains any captured DLX name and
  reports undetermined routing when the evidence cannot establish a route.
- Policy responses and definitions exports preserve legacy queue leader
  locator values, including `min-masters` in exported queue arguments.
- Shovel topology requires a valid direct URI form to confirm a hostless
  endpoint, preventing malformed URIs from adding false loop candidates.
- AMQP 1.0 shovel topology preserves the socket host, resolves query vhosts
  using broker decoding rules, and handles v1 and v2 address escapes separately.
  Malformed endpoint evidence leaves the affected hop unresolved.

## [0.5.0] - 2026-11-15

### Added

- Fanout results retain captured queue durability and expose derived direct
  target counts, durability counts, and quorum-member totals through the
  existing `fanout_exchange_bindings()` method and audit report. Partial
  membership evidence remains available; complete totals are `None` when
  target types or quorum memberships are unknown. Capacity thresholds remain
  caller policy.
- `ClusterAuditor.exchange_routing_coverage()` evaluates every contained
  exchange for an optional routing key, reporting possible destinations,
  no destination, or explicit uncertainty. The aggregate audit includes
  coverage with an unknown key. Results account for exchange chains and
  conditional alternate routes while preserving the existing degree-based
  `black_hole_exchanges()` contract. Nonempty header predicates remain
  undetermined.
- `ClusterAuditor.dead_letter_coverage()` reports effective DLX configuration
  and routing evidence for every captured queue, also included in `audit()`.
  Results distinguish absent settings, missing exchanges, no matching
  destination, possible destinations, and undetermined routing with reasons.
  Routing accounts for direct, topic, fanout, and decidable headers bindings,
  conditional alternate exchanges, and default-exchange queue routes. Terminal
  queues remain visible; streams are explicitly not applicable.
- `ClusterAuditor.queue_type_inventory()` groups captured queues by type across
  the cluster and per virtual host, with derived counts and retained queue
  identities. The immutable inventory is also included in `audit()`. Captured
  vhost defaults and empty vhosts survive graph serialization; missing defaults
  remain unknown and do not override existing queue types.
- A [RabbitMQ 4.3 complex-topology demo](tests/integration/rmq-4.3/complex-topology/README.md)
  with a full definitions export, queue/exchange runtime snapshots, and expected
  topology audit findings.

### Changed

- Raised the minimum HTTPX2 version to 2.16.0.
- Response models parse wire field names. Reload response dumps using Python
  field names with `by_name=True`.
- Response argument maps, policy definitions, and exported vhost limits expose
  their validated dictionaries through `.root`, retaining typed attributes.
  For Python-name construction, replace calls such as
  `ExportQueueArguments(dead_letter_exchange="dlx")` with
  `ExportQueueArguments.from_fields(dead_letter_exchange="dlx")`. Ordinary
  constructors and validation methods interpret literal wire keys.
- Message-loop candidate searches skip residual cyclic components that no
  longer contain a republishing hop, avoiding unnecessary enumeration without
  changing findings or cycle limits.

### Fixed

- Captured argument, policy, and vhost-limit maps preserve custom keys that
  resemble Python attribute names. Keys such as `dead_letter_exchange`,
  `alternate_exchange`, and `x_match` no longer create routing settings or
  rename headers; absent wire aliases are not invented during serialization.
- Shovel topology treats AMQP 1.0 v2 exchange addresses as destinations only.
  Empty AMQP 0-9-1/local source keys and legacy AMQP 1.0 destination keys are no
  longer reported as fixed empty routing keys.
- Cluster definitions accept explicit null global-parameter values while
  continuing to reject missing values.

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

[Unreleased]: https://github.com/brianmori/rabbitmq-management-sdk/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/brianmori/rabbitmq-management-sdk/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/brianmori/rabbitmq-management-sdk/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/brianmori/rabbitmq-management-sdk/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/brianmori/rabbitmq-management-sdk/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/brianmori/rabbitmq-management-sdk/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/brianmori/rabbitmq-management-sdk/releases/tag/v0.1.0
