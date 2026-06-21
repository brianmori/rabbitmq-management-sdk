# rabbitmq-management-sdk

A modern, fully-typed Python SDK for the [RabbitMQ HTTP Management API](https://www.rabbitmq.com/docs/management).
Declare, inspect, and manage queues, exchanges, bindings, virtual hosts, limits, and shovels with
Pydantic-validated models and a clean, predictable error hierarchy.

[![CI](https://github.com/brianmori/rabbitmq-management-sdk/actions/workflows/ci.yaml/badge.svg)](https://github.com/brianmori/rabbitmq-management-sdk/actions/workflows/ci.yaml)
[![PyPI](https://img.shields.io/pypi/v/rabbitmq-management-sdk.svg)](https://pypi.org/project/rabbitmq-management-sdk/)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://pypi.org/project/rabbitmq-management-sdk/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://img.shields.io/badge/mypy-checked-blue.svg)](https://mypy-lang.org/)

> ⚠️ **Status: Alpha (0.x).** The API may change between minor versions until 1.0.

---

## Why this SDK?

The RabbitMQ Management API is a flat HTTP/JSON surface using hyphenated keys (`x-queue-type`,
`dead-letter-exchange`), `%2F`-encoded vhosts, and per-resource quirks. This SDK wraps it so you get:

- **Typed requests and responses** — Pydantic v2 models with field validation, discriminated unions
  for queue/shovel variants, and IDE autocomplete instead of raw dicts.
- **An exception hierarchy** — every failure is a `RabbitMQError`; HTTP status codes map to
  specific subclasses (`NotFoundError`, `ConflictError`, `PreconditionFailedError`, …). `httpx`,
  `json`, and `pydantic` exceptions never leak.
- **Automatic version detection** — the client reads the broker version and routes to the right
  resource managers (with an override for proxied setups).
- **Resilience built in** — connection pooling and exponential-backoff retries via `httpx`.
- **Ships type information** — `py.typed` is included, so downstream `mypy` sees the annotations.

## Features

| Resource | Operations                                                                             |
|---|----------------------------------------------------------------------------------------|
| **Queues** | Create / Get / Delete — classic, quorum, and stream types with type-specific arguments |
| **Exchanges** | Create / Get / Delete / list (per-vhost and cluster-wide)                              |
| **Bindings** | Create / List / Delete exchange→queue and exchange→exchange                            |
| **Virtual hosts** | Create / Get / Delete, deletion-protection, per-vhost limits                           |
| **Shovels** | Create / Get / Delete, status queries — AMQP 0-9-1, AMQP 1.0, and `local` protocols    |

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
from rabbitmq_management_sdk import Config, RabbitMQClient

# On construction the client calls GET /api/overview to detect the broker
# version and route to the matching resource managers.
client = RabbitMQClient(
    Config(host="localhost", port=15672, username="guest", password="guest")
)

print("Connected to RabbitMQ", client.version)
```

> The broker must be reachable when the client is constructed (that is when version detection runs).
> Behind a proxy that strips version headers? Pin it with
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

Every error raised by the SDK derives from `RabbitMQError`, a single `except` is enough to be
safe — and you can narrow to specific HTTP outcomes when you need to.

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
└── MalformedResponseError      (success status, unexpected body)
```

## Strict vs. compatibility mode

`Config(strict=...)` controls how default-valued fields are sent on `PUT` (declare) calls:

- **`strict=False` (default)** — default values are omitted, so re-declaring an existing resource
  won't trip a `406 Precondition Failed` conflict. Best for idempotent provisioning.
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

Live tests read credentials from a `.env` file (see [`.env.example`](.env.example)) and can run
against a disposable broker:

```bash
just test-live-rmq42     # spins up RabbitMQ 4.2 via Docker, runs live tests, tears down
just test-live-rmq43     # same, for RabbitMQ 4.3
```

## Contributing

Contributions are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) and the
[Code of Conduct](CODE_OF_CONDUCT.md). To report a security issue, see [SECURITY.md](SECURITY.md).

## License

Licensed under the [Apache License 2.0](LICENSE).
