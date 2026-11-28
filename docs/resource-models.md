# Request and response models

Request models help Python callers build declarations. Response models read
RabbitMQ's JSON. Those inputs have different naming rules, even when they
describe the same resource.

## Request construction

Requests accept Python field names and wire aliases. Managers serialize them
with wire aliases before sending them to RabbitMQ:

```python
from rabbitmq_management_sdk import ClassicQueueRequest, QueueRequest

request = QueueRequest(
    arguments=ClassicQueueRequest(dead_letter_exchange="failures"),
)
body = request.model_dump(by_alias=True, exclude_none=True)
assert body["arguments"]["x-dead-letter-exchange"] == "failures"
```

The manager may further apply its documented strict/compatibility behavior.

Stream declarations and new policy definitions share RabbitMQ's case-sensitive
retention age format. Use `7D` for seven days. To change age and byte retention
on a running stream, see the [management HTTP lifecycle example](stream-lifecycle.md).

## Broker version checks on writes

`RabbitMQClient` selects managers by API major version. The tested v4 brokers
are RabbitMQ 4.2.9 and 4.3.8. Managers obtained from the client use its detected
broker version or configured `version_override` for write checks. Queue,
regular policy, and operator-policy writes check delayed retry and
disconnected-consumer timeout settings. Shovel writes check
`src-consumer-name` for all source protocols. When the serialized payload
contains a guarded setting and the broker is older than 4.3.0, the SDK raises
`UnsupportedBrokerFeatureError` before the HTTP write. Checks apply in both
serialization modes. Unset values and custom policy keys retain their normal
serialization behavior.

Directly constructed queue, policy, operator-policy, and shovel managers can
receive a `RabbitMQVersion` through `broker_version`. Without a version, those
managers send the payload to RabbitMQ for handling. Include the broker version
when callers need early rejection of known incompatible fields. RabbitMQ still
checks permission and feature availability. Offline topology audits use the
broker version recorded in the capture. [Write compatibility tests](../tests/integration/test_versioned_writes.py)

## Response parsing

Responses recognize the exact wire names. A captured argument named
`dead_letter_exchange` is an ordinary custom argument; only
`x-dead-letter-exchange` declares a queue's dead-letter exchange.

Definitions argument maps, exchange observations' argument maps, policy
definitions, and exported vhost limits use native Pydantic dictionary schemas.
Known wire keys are typed, while additional keys accept any JSON value:

```python
from rabbitmq_management_sdk.resources.v4.admin.schemas.export_response import (
    ClusterDefinitionsResponse,
)

captured = {
    "queues": [{
        "name": "orders",
        "vhost": "/",
        "durable": True,
        "auto_delete": False,
        "arguments": {
            "x-queue-type": "classic",
            "x-dead-letter-exchange": "failures",
            "dead_letter_exchange": {"custom": [None, "data"]},
        },
    }],
}
definitions = ClusterDefinitionsResponse.model_validate(captured)
arguments = definitions.queues[0].arguments

assert arguments.dead_letter_exchange == "failures"
assert arguments.root == captured["queues"][0]["arguments"]
assert definitions.model_dump(by_alias=True, exclude_unset=True) == captured
```

The validated dictionary is the source for serialization and typed properties.
Custom keys are not renamed, absent keys are not invented, and ordinary dumps
retain explicit nulls. Known values follow Pydantic's usual validation and
coercion rules. Queue observations continue to use their separate, typed
per-queue-type response models.

Policy responses and definitions exports preserve the legacy
`queue-leader-locator` values `random` and `least-leaders` exactly as captured.
Exported `x-queue-leader-locator` queue arguments also preserve `min-masters`.
The policy request model uses `client-local` and `balanced` for new policies.
See RabbitMQ 4.3.6's [policy validation](https://github.com/rabbitmq/rabbitmq-server/blob/v4.3.6/deps/rabbit/src/rabbit_policies.erl#L174-L184)
and [queue leader locator values](https://github.com/rabbitmq/rabbitmq-server/blob/v4.3.6/deps/rabbit/src/rabbit_queue_location.erl#L30-L39).

`QueueResponse` also retains the optional top-level queue placement fields
`members`, `leader`, and `online`. Topology construction copies these lists
into immutable evidence for declared quorum queues. See
[quorum resilience capture inputs](quorum-resilience.md#capture-and-interpret-the-evidence).

Unknown resource metadata is normally ignored for forward compatibility.
Extensible dictionaries have their own preservation rules instead of applying
`extra="allow"` to every response.

## Constructing and serializing argument maps

Use the explicit `from_fields()` factory for Python-name construction:

```python
from rabbitmq_management_sdk.resources.v4.admin.schemas.export_response import (
    ExportQueueArguments,
)

arguments = ExportQueueArguments.from_fields(dead_letter_exchange="failures")
assert arguments.model_dump() == {"x-dead-letter-exchange": "failures"}

literal = ExportQueueArguments.from_wire({"dead_letter_exchange": "custom"})
assert literal.dead_letter_exchange is None
assert literal.model_dump() == {"dead_letter_exchange": "custom"}
```

`from_fields()` translates recognized Python names and then validates the
complete result through Pydantic, including additional plugin keys. Migrate
`ExportQueueArguments(dead_letter_exchange="failures")` to the factory above.
This migration also applies to the other response argument maps, response
policy definitions, and `VhostExportLimits`. Request constructors are unchanged.

For captured dictionaries, use `model_validate()` or `model_validate_json()`;
`from_wire()` is a thin alias for `model_validate()`. Ordinary constructors
also interpret literal wire keys. These paths share Pydantic's normal schema
validation, without a custom constructor or a separate validation pass.
Argument maps are `RootModel` values: `.root` holds the validated dictionary
and typed properties read it directly.

Argument-map dumps always contain dictionary keys in their wire spelling,
regardless of `by_alias`. `include` and `exclude` select dictionary keys.
`exclude_none=True` follows Pydantic's TypedDict serialization rules and removes
null entries. To preserve captured nulls, use ordinary dumps or
`model_dump(by_alias=True, exclude_unset=True)` without `exclude_none=True`.

Model freezing is shallow. It prevents reassigning fields or typed properties,
but it does not make nested dictionaries and lists immutable. Existing
argument-map instances are revalidated when passed through a map validation
entry point.

## Strict validation

Python, JSON, nested response, and `TypeAdapter` validation use the same native
schema. Runtime options such as `strict=True` are not replaced by an internal
validation call:

```python
from pydantic import ValidationError
from rabbitmq_management_sdk.resources.v4.admin.schemas.export_response import (
    ExportQueueArguments,
)

assert ExportQueueArguments.model_validate({"x-max-length": "100"}).max_length == 100
try:
    ExportQueueArguments.model_validate({"x-max-length": "100"}, strict=True)
except ValidationError:
    pass
else:
    raise AssertionError("strict validation must reject a numeric string")
```

This is Pydantic's strict validation, separate from the SDK client's `strict`
setting that controls declaration serialization.

## Reloading Python-name dumps

Response validation now defaults to wire names. To reload a response dumped
with Python field names, explicitly opt into those names:

```python
from rabbitmq_management_sdk import PolicyResponse

response = PolicyResponse.model_validate({
    "name": "limits",
    "vhost": "/",
    "pattern": "^orders$",
    "priority": 1,
    "apply-to": "queues",
    "definition": {"max-length": 100},
})
python_data = response.model_dump()
assert PolicyResponse.model_validate(python_data, by_name=True) == response

wire_json = response.model_dump_json(by_alias=True)
assert PolicyResponse.model_validate_json(wire_json) == response
```

This opt-in does not reinterpret keys inside captured argument or policy maps.
Use request models when constructing new declarations. Direct Pydantic
validation raises `ValidationError`; managers and captured-file loading retain
their documented SDK exception boundaries.

## Implementation

`resources/base.py` keeps shared value settings in `RabbitMQBase`, request
naming rules in `RabbitMQRequest`, and wire parsing rules in
`RabbitMQResponse`. Domain models may specialize unknown-field handling.

`resources/_argument_map.py` supplies a `RootModel` and a PEP 728 TypedDict base
whose additional entries are `JsonValue`. Domain TypedDicts declare optional
keys in their exact wire spelling. Pydantic validates both the declared keys
and the additional JSON entries in its ordinary pipeline. Export and API
policy responses share the same schema. Typed accessors only read validated
data; no field-set mutation, custom constructor, or custom serializer is used.

Regression coverage is in `tests/unit/test_resource_models.py`,
`tests/unit/test_export_response.py`, and
`tests/integration/test_topology_argument_maps.py`. The complex topology
captures verify that the resulting graph and findings remain unchanged.
