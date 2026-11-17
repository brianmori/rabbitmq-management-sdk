# Test suite

Run the non-live suite and static checks with an explicit Python version:

```bash
UV_PYTHON=3.12 just check
UV_PYTHON=3.12 just test
```

Run the complete live suite on disposable RabbitMQ 4.2 and 4.3 brokers:

```bash
UV_PYTHON=3.12 just test-live
```

The version-specific recipes are `just test-live-rmq42` and
`just test-live-rmq43`. They supply test credentials and remove their broker,
network, and volumes afterward. Direct live pytest runs use the environment
and `.env`; use a disposable broker. The live tests publish and consume test
messages and create and remove resources.

## What each layer proves

| Location | Responsibility |
| --- | --- |
| `unit/` | Model invariants, request validation, client lifetime, retry behavior, parser rules, and graph algorithms on small inputs |
| `integration/` | Actual manager/adapter calls against mocked HTTP, SDK exception boundaries, encoded paths, pagination, and real captured response formats |
| `live/` | Broker acceptance, SDK-created resource read-back and deletion, actual message routing, and topology analysis of freshly imported resources |
| `shared/` | Small input builders, explicit complex-scenario expectations, and bounded HTTP message helpers |

Similar cases across layers are intentional when they test different contracts.
For example, a request model may raise `pydantic.ValidationError` directly,
while malformed data returned through a manager must raise an SDK exception.
Keep both cases. Also retain the independent brute-force cycle tests, broker
version captures, and both statistics/no-statistics response variants.

`unit/test_resource_models.py` checks the request/response naming boundary,
literal argument-map round trips, strict Python/JSON/TypeAdapter validation,
existing-instance revalidation, and the explicit `from_fields()` factory.
`integration/test_topology_argument_maps.py`
checks the same captured evidence through HTTP, files, and in-memory topology
construction. Python-name response dumps opt in with `by_name=True`; captured
argument dictionaries always retain exact wire keys.

Prefer extending an existing parameter matrix when inputs share a contract.
Keep separate tests when the failure boundary, scope, provenance, or expected
behavior differs. A test should assert observable behavior; an unconditional
mock success or a live call without result assertions adds little coverage.

## Resource-manager tests

Quorum retry and timeout configuration is covered by the queue/policy model
and manager suites, `integration/test_quorum_configuration_captures.py`, and
`live/test_quorum_configuration.py`. The live module gates 4.3-only cases by
the detected broker version; the rest of the live suite still runs on 4.2.
The [4.3.6 evidence bundle](integration/rmq-4.3/quorum-configuration/README.md)
documents the opt-in capture process and sanitization. Configuration read-back
does not test retry timing or partition recovery.

`unit/test_parser_topology.py` checks every retry selector through queue
arguments and selected policy, with and without a DLX cycle. The settings
preserve graph identities, route edges, and cycle paths while retaining the
new configuration attributes. They do not create redelivery self-edges or
suppress cycle candidates. The focused capture suite also loads
the unmodified 4.3.6 definitions projection through `ClusterAuditor.from_files()`
and verifies retained configuration without invented routes or unnecessary
policy-selection evidence.

Quorum delivery audit values and resolution are covered by `unit/test_models.py`
and `unit/test_quorum_configuration.py`. The focused 4.3.6 bundle includes
selected regular/operator policies and effective policy definitions; replay
tests check source evidence and resolved settings through the auditor facade.
`live/test_quorum_audit.py` runs on both 4.2 and 4.3. The 4.3-only configuration
module also verifies argument/policy precedence. Global timeout defaults remain
unknown when not captured, and queue settings do not imply consumer activity.

Mocked tests call the manager method under test and inspect the exact HTTP
method, encoded path, request count, and significant wire fields. The exception
matrix verifies the specific SDK class, public base exception, retained cause,
and error metadata in the same cases.

Live manager tests use minimal scenarios. The dead-letter test creates a DLX,
work queue, parking queue, and binding through SDK managers, reads the settings
back, rejects a message, verifies parking delivery, and verifies deletion. It
needs no routing loop. Shovel manager tests verify a running worker and actual
delivery for both AMQP 0-9-1 and AMQP 1.0 before checking deletion.

## Live fixture ownership

`tests/conftest.py` provides immutable, session-scoped connection settings and
a cluster-scoped client fixture. Importing or collecting the suite does not
create broker resources.

`live/conftest.py` provides explicit fixtures:

- `vhost_factory(request)` creates a uniquely named vhost, registering its
  cleanup before creation. Pass `None` for the default `VhostRequest`.
- `live_vhost` provides one such vhost.
- `live_client` owns a client scoped to `live_vhost` and closes it before the
  vhost is removed.
- `management_api` owns a module-scoped adapter for publishing/getting test
  messages and other broker evidence outside the SDK resource API.

There is no autouse vhost setup and no dependency on shared `test-src` or
`test-dst` resources. Tests that manage their own special vhost names retain
their explicit cleanup. Suppress only an expected already-absent resource
during cleanup; other cleanup failures should remain visible.

## Complex topology captures

Exchange routing coverage is tested in `unit/test_exchange_routing.py`,
`integration/test_exchange_routing_captures.py`, and
`live/test_exchange_routing_coverage.py`. The existing DLX selector tests cover
the shared traversal, while the new tests focus on publication contexts,
degree-only compatibility, exchange-chain dead ends, and conditional alternates.
The process-transfer test carries both exchange and DLX results.

Effective DLX coverage has focused tests in `unit/test_dead_letter.py`,
`integration/test_dead_letter_captures.py`, and
`live/test_dead_letter_coverage.py`. Live cases compare key and header findings
with broker routing, verify argument/policy precedence, exercise alternate
suppression, and deliver rejected messages through configured and default DLXs.
Each case uses disposable vhosts and cleanup; captures are not regenerated.

The [RabbitMQ 4.3 demo bundle](integration/rmq-4.3/complex-topology/README.md)
contains a one-time full definitions export and complete queue/exchange API
observations. Ordinary tests never regenerate these files. The observations
are needed for broker-selected policies and captured quorum membership.

Fanout capacity coverage lives in `unit/test_fanout.py`, the result-model and
facade tests, and `integration/test_fanout_captures.py`. It distinguishes
durable, non-durable, and unknown targets; partial membership; dangling queue
types; and complete zero-member sums for known non-quorum targets. The shared
live demo checks durability and capacity metrics from real exports and queue
membership observations. Counts deduplicate parallel bindings and include
only direct queue targets.

The offline capture tests and live import tests share the expectations in
`shared/complex_topology.py`. They assert directed paths with multiplicity,
SCC membership, healthy routes, policy precedence, degree findings, shovel
advisories, effective DLX routing, contextual exchange coverage, the complete
fanout exchange-to-queue map and capacity counts, and queue type counts with
per-vhost defaults. Cycle caps cover
unlimited, partial, exact, and larger-than-result bounds. Reordered capture
copies in pytest's temporary directory verify deterministic graphs and reports.

Queue inventory also has focused coverage in `unit/test_queue_inventory.py`
and `integration/test_queue_inventory_captures.py`. The live test in
`live/test_queue_inventory.py` changes a populated vhost's default and checks
that its existing queue types remain distinct from that default, while an
empty vhost stays in the inventory. These tests do not rewrite captures.

The live fixture imports only resource sections into uniquely named vhosts.
It supplies the configured credentials to shovels in memory, reads fresh queue
and exchange observations, and cleans up the imported resources. It does not
replace broker users or cluster parameters. The intentional loop scenarios
remain empty; routing checks use healthy paths and bounded poison-message
handling.
