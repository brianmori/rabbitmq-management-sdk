# rabbitmq-management-sdk

A modern, fully-typed Python SDK for the [RabbitMQ HTTP Management API](https://www.rabbitmq.com/docs/management).
Declare, inspect, and manage queues, exchanges, bindings, virtual hosts, limits, policies, operator policies, and
shovels. Audit exported topologies for routing cycles and structural risks with Pydantic-validated models and a
clean, predictable error hierarchy.

[![CI](https://github.com/brianmori/rabbitmq-management-sdk/actions/workflows/ci.yaml/badge.svg)](https://github.com/brianmori/rabbitmq-management-sdk/actions/workflows/ci.yaml)
[![PyPI](https://img.shields.io/pypi/v/rabbitmq-management-sdk.svg)](https://pypi.org/project/rabbitmq-management-sdk/)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://pypi.org/project/rabbitmq-management-sdk/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://img.shields.io/badge/mypy-checked-blue.svg)](https://mypy-lang.org/)

> ⚠️ **Status: Alpha (0.x).** The API may change between minor versions until 1.0.

---

## Why this SDK?

RabbitMQ exposes configuration as flat resources, but the operational questions
are graph-shaped: Where can a message travel? Can dead-lettering or a shovel
send it back to where it started? Which declared routes point to missing
resources? This SDK turns captured configuration into evidence you can inspect,
test, and audit:

- **Reconstruct routing topology** — build an immutable graph of exchanges,
  queues, bindings, dead-letter routes, alternate exchanges, and confirmed-local
  shovel hops from a definitions export and optional resource observations.
- **Find actionable routing risks** — distinguish structural cycles from
  message-loop candidates that cross a dead-letter or shovel republishing
  boundary, with bounded searches and explicit truncation reporting.
- **Audit more than cycles** — report dangling routes, black-hole exchanges,
  unreachable internal exchanges, queues without captured ingress, cross-vhost
  shovels, and unresolved or unconfirmed shovel endpoints.
- **Respect broker evidence** — direct arguments take precedence, while complete
  queue and exchange observations identify broker-selected policies without
  reimplementing RabbitMQ's policy matching rules in Python.
- **Run deterministic offline analysis** — load sanitized captures from files,
  retain stable cluster identity, produce consistently ordered findings, and
  serialize trusted topology values for analysis in another process.
- **Manage the same resources with typed APIs** — Pydantic v2 request and
  response models cover queues, exchanges, bindings, virtual hosts, limits,
  policies, operator policies, and shovels while preserving plugin-defined
  values where RabbitMQ is extensible.
- **Keep failures predictable** — transport, HTTP, response-parsing,
  topology-loading, and topology-analysis failures share the `RabbitMQError`
  hierarchy; the client also provides version routing, connection pooling, and
  exponential-backoff retries.

## Features

| Resource | Operations                                                                             |
|---|----------------------------------------------------------------------------------------|
| **Queues** | Create / Get / List / Delete — classic, quorum, and stream types with type-specific arguments |
| **Exchanges** | Create / Get / Delete / list (per-vhost and cluster-wide)                              |
| **Bindings** | Create / List / Delete exchange→queue and exchange→exchange                            |
| **Virtual hosts** | Create / Get / Delete, deletion-protection, per-vhost limits                           |
| **Policies** | Create / Get / List / Delete regular and operator policies with typed core settings and preserved plugin keys |
| **Shovels** | Create / Get / Delete, status queries — AMQP 0-9-1, AMQP 1.0, and `local` protocols    |
| **Topology audit** | Detect routing cycles, dangling hops, unreachable internal exchanges, and shovel vhost boundaries |

## Requirements

- **Python 3.12+**
- **RabbitMQ 4.x** with the management plugin enabled
  (`rabbitmq-plugins enable rabbitmq_management`)

## Installation

```bash
pip install rabbitmq-management-sdk
# or
uv add rabbitmq-management-sdk
```

## Quickstart

```python
from rabbitmq_management_sdk import Config, RabbitMQClient, RabbitMQVersion

# On construction the client calls GET /api/overview to detect the broker
# version and route to the matching resource managers.
client = RabbitMQClient(
    Config(host="localhost", port=15672, username="guest", password="guest")
)

print("Connected to RabbitMQ", client.version)
```

> The broker must be reachable when the client is constructed (that is when version detection runs).
> If `/api/overview` is unavailable or its `rabbitmq_version` field is unusable, pin the version with
> `Config(..., version_override=RabbitMQVersion.parse("4.3.0"))`.

## Usage

### Queues

```python
from rabbitmq_management_sdk import ClassicQueueRequest, QueueRequest, QuorumQueueRequest

# Classic queue with a 60-second message TTL
client.queues.create(
    "orders",
    QueueRequest(arguments=ClassicQueueRequest(message_ttl=60_000)),
)

# Replicated quorum queue with a delivery limit
client.queues.create(
    "payments",
    QueueRequest(arguments=QuorumQueueRequest(delivery_limit=5)),
)

queue = client.queues.get("orders")
print(queue.state)          # e.g. "running"

client.queues.delete("orders")
```

### Exchanges

```python
from rabbitmq_management_sdk import ExchangeRequest, ExchangeType

client.exchanges.create("events", ExchangeRequest(type=ExchangeType.TOPIC))
exchange = client.exchanges.get("events")
client.exchanges.delete("events")
```

### Bindings

```python
from rabbitmq_management_sdk import BindingRequest

binding = client.bindings.create_exchange_to_queue(
    exchange="events",
    queue="orders",
    request=BindingRequest(routing_key="orders.*"),
)

# The server-assigned properties_key identifies a specific binding for deletion.
client.bindings.delete_exchange_to_queue("events", "orders", binding.properties_key)
```

### Virtual hosts & limits

```python
from rabbitmq_management_sdk import VhostLimitName, VhostLimitRequest, VhostRequest

client.admin.create_vhost("billing", VhostRequest(description="Billing services"))

client.admin.apply_vhost_limit(
    "billing", VhostLimitName.MAX_CONNECTIONS, VhostLimitRequest(value=100)
)

limits = client.admin.get_vhost_limits("billing")  # VhostLimitResponse | None
```

### Policies

Regular and operator policies are scoped to the virtual host configured on the
client. `list_by_vhost()` lists that virtual host; `list_all()` lists policies
across the cluster.

```python
from rabbitmq_management_sdk import (
    OperatorPolicyApplyTo,
    OperatorPolicyRequest,
    PolicyApplyTo,
    PolicyDefinition,
    PolicyRequest,
)

policy_request = PolicyRequest(
    pattern=r"^orders\.",
    definition=PolicyDefinition(
        dead_letter_exchange="orders.dlx",
        message_ttl=60_000,
    ),
    priority=10,
    apply_to=PolicyApplyTo.QUEUES,
)
client.policies.create("orders-policy", policy_request)

client.operator_policies.create(
    "queue-limit",
    OperatorPolicyRequest(
        pattern=".*",
        definition=PolicyDefinition(max_length=100_000),
        priority=100,
        apply_to=OperatorPolicyApplyTo.QUEUES,
    ),
)

for policy in client.policies.list_by_vhost():
    print(policy.name, policy.definition)
```

Resource managers serialize policy requests automatically. Known core settings
are typed, while unknown definition keys are retained so settings contributed
by RabbitMQ plugins or newer broker versions survive validation and wire
serialization:

```python
plugin_definition = PolicyDefinition.model_validate(
    {
        "dead-letter-exchange": "orders.dlx",
        "federation-upstream-set": "all",
    }
)

assert plugin_definition.model_dump(by_alias=True, exclude_none=True) == {
    "dead-letter-exchange": "orders.dlx",
    "federation-upstream-set": "all",
}
```

### Shovels

```python
from rabbitmq_management_sdk import (
    Amqp091ShovelDestination,
    Amqp091ShovelSource,
    ShovelRequest,
)

client.shovels.create(
    "archive",
    ShovelRequest(
        src_uri="amqp://localhost",
        dest_uri="amqp://backup-host",
        src_arguments=Amqp091ShovelSource(src_queue="orders"),
        dest_arguments=Amqp091ShovelDestination(dest_queue="orders-archive"),
    ),
)

for status in client.shovels.get_all_shovel_statuses():
    print(status)
```

### Topology auditing

Use `ClusterAuditor` to inspect a RabbitMQ definitions export for circular
message routes and other configuration risks. You do not need to understand
graphs to use it.

```python
from rabbitmq_management_sdk import ClusterAuditor

auditor = ClusterAuditor(
    client.admin.export_definitions(),
    queues=client.queues.list_all(disable_stats=True),
    exchanges=client.exchanges.list_all(disable_stats=True),
    cluster_label="production-eu-west-1",
    in_cluster_amqp_hosts={"rabbit-1.internal", "rabbit-2.internal"},
)

report = auditor.audit(max_cycles=100)
for cycle in report.message_loop_candidates.cycles:
    print("Possible message loop:", cycle)

if report.message_loop_candidates.truncated:
    print("More message-loop candidates exist.")
if report.structural_cycles.truncated:
    print("More structural cycles exist.")

# Immutable, presentation-agnostic nodes and edges for other consumers.
topology = auditor.topology

# Complete SCC partition plus the cycle-containing subset.
components = auditor.strongly_connected_components()
cyclic_components = auditor.cyclic_components()
```

Queue and exchange observations let the auditor use the broker-selected user
policy for dead-letter and alternate-exchange routes. This avoids
reinterpreting RabbitMQ policy regular expressions locally. The exchange
observations also resolve referenced predeclared, system, and plugin exchanges
that RabbitMQ omits from definitions exports.

The [topology documentation index](docs/README.md) routes readers to the user
guide, graph-construction guide, concise cycle-analysis guide, or detailed
Tarjan and Johnson tutorial.

#### Topology serialization

`ClusterTopology` values can be serialized with `pickle` for trusted storage or
transfer to another Python process. Derived lookup caches are rebuilt when the
topology is loaded, and `ClusterAuditor.from_topology()` provides the same
analysis facade without requiring the original definitions export:

```python
import pickle

payload = pickle.dumps(auditor.topology, protocol=pickle.HIGHEST_PROTOCOL)
restored_topology = pickle.loads(payload)  # Only load data from a trusted source.
restored_auditor = ClusterAuditor.from_topology(restored_topology)

report = restored_auditor.audit(max_cycles=100)
```

An auditor reconstructed this way does not retain the definitions export, so
its `definitions` property is unavailable. Use the same SDK version when
loading a serialized topology; cross-version pickle compatibility is not
guaranteed. Never unpickle untrusted data.

## Authentication

Provide **either** Basic Auth **or** mutual TLS — the configuration rejects both or neither.

```python
from rabbitmq_management_sdk import Config, RabbitMQClient, SSLConfig

# Basic Auth (default)
client = RabbitMQClient(Config(host="localhost", username="guest", password="guest"))

# Mutual TLS (client certificate; omit username/password)
client = RabbitMQClient(
    Config(
        host="rabbit.internal",
        port=15671,
        username=None,
        password=None,
        ssl_context=SSLConfig(
            client_cert=("certs/client.pem", "certs/client.key"),
            ca_bundle="certs/ca.pem",
        ),
    )
)
```

## Error handling

Use `RabbitMQError` to catch HTTP request, transport, response-parsing,
topology-loading, and topology-analysis failures. You can narrow it to
specific HTTP outcomes when needed. Validation performed while you directly
construct a Pydantic request or configuration model uses
`pydantic.ValidationError`.

```python
from rabbitmq_management_sdk import ConflictError, NotFoundError, RabbitMQError

try:
    client.queues.get("does-not-exist")
except NotFoundError:
    ...  # HTTP 404
except ConflictError:
    ...  # HTTP 409 — conflicting redeclaration
except RabbitMQError as exc:
    ...  # transport/timeout/malformed-response and any other SDK error
```

```text
RabbitMQError
├── TransportError              (no HTTP response)
│   ├── TimeoutError
│   └── ConnectionError
├── APIError                    (HTTP status >= 400)
│   ├── BadRequestError              400
│   ├── UnauthorizedError            401
│   ├── ForbiddenError               403
│   ├── NotFoundError                404
│   ├── MethodNotAllowedError        405
│   ├── ConflictError                409
│   ├── PreconditionFailedError      412
│   ├── UnprocessableEntityError     422
│   ├── TooManyRequestsError         429
│   └── ServerError                  5xx
│       └── ServiceUnavailableError  503
├── MalformedResponseError      (success status, unexpected body)
└── TopologyError               (definitions-export topology analysis)
    ├── TopologyLoadError       (file I/O, decoding, or JSON errors)
    ├── TopologyDefinitionsError (definitions do not match the wire schema)
    ├── TopologyResourceSnapshotError (queue or exchange dump has invalid data)
    ├── TopologyParseError      (validated definitions cannot form a graph)
    ├── TopologyValidationError (inconsistent caller-constructed graph model)
    └── TopologyAnalysisError   (invalid topology-analysis request)
```

## Strict vs. compatibility mode

`Config(strict=...)` controls how default-valued fields are sent on `PUT` (declare) calls:

- **`strict=False` (default)** — default values are omitted, reducing
  redeclaration conflicts when an existing resource uses broker defaults.
  Best for idempotent provisioning.
- **`strict=True`** — every field is sent explicitly. Best for fresh deployments where you want the
  broker state to match your request exactly.

## Development

This project uses [uv](https://docs.astral.sh/uv/) and [just](https://github.com/casey/just).

```bash
uv sync --group dev      # create the environment with dev tooling
just check               # ruff lint + format check + mypy
just fix                 # ruff auto-fix + reformat
just test                # unit + integration tests (no broker required)
just audit               # pip-audit dependency vulnerability scan
```

### Testing

| Marker | Location | Needs a broker? |
|---|---|---|
| `unit` | `tests/unit/` | no |
| `integration` | `tests/integration/` | no (uses `httpx.MockTransport`) |
| `live` | `tests/live/` | yes |

The Just recipes start disposable brokers and configure their test credentials
automatically:

```bash
just test                # runs unit and integration tests without a broker
just test-live           # runs live tests against RabbitMQ 4.2 and 4.3
just test-live-rmq42     # spins up RabbitMQ 4.2 via Docker, runs live tests, tears down
just test-live-rmq43     # same, for RabbitMQ 4.3
```

To run the live tests directly against an existing broker, copy
[`.env.example`](.env.example) to `.env`, adjust the connection settings, and
run `uv run pytest -m live`.

## Contributing

Contributions are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) and the
[Code of Conduct](CODE_OF_CONDUCT.md). To report a security issue, see [SECURITY.md](SECURITY.md).

## License

Licensed under the [Apache License 2.0](LICENSE).
