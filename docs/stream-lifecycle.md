# Manage a stream over HTTP

The [runnable example](../examples/stream_lifecycle.py) creates a durable stream,
reads its declaration, and lists streams in the same virtual host. It changes
retention from one day and 1,000,000 bytes to seven days and 2,000,000 bytes,
then checks the effective policy reported by RabbitMQ and cleans up.

## Run the example

Use Python 3.12 or later and a disposable RabbitMQ 4.2 or 4.3 broker with the
management plugin enabled. Create the named virtual host first and give the
management user permission to configure queues and regular policies there.
From the SDK checkout, set `RABBIT_USER` and `RABBIT_PASS` in your environment.
`RABBIT_HOST` and `RABBIT_PORT` default to `localhost` and `15672`.

```bash
uv run python examples/stream_lifecycle.py stream-lab 'history.[eu]+/orders?#%' history-retention --priority 20
```

Choose unused queue and policy names. Pass the names as raw strings. The SDK
encodes URL path segments and escapes the stream name separately for the policy
pattern. `client.queues.list_streams(page_size=1)` fetches all queue pages in the
configured virtual host and returns only entries whose type is `stream`.
`page_size` counts queues per HTTP request.

## Change the retention of an existing stream

Call `client.policies.set_stream_retention(policy_name, stream_name=stream_name,
max_age="7D", max_length_bytes=2_000_000, priority=20)` with the raw names.
The example reads back and prints the complete regular policy including its
scope, priority, and definition.

```json
{
  "name": "history-retention",
  "vhost": "stream-lab",
  "pattern": "\\Ahistory\\.\\[eu\\]\\+/orders\\?\\#%\\z",
  "apply-to": "streams",
  "priority": 20,
  "definition": {
    "max-age": "7D",
    "max-length-bytes": 2000000
  }
}
```

An existing policy with the same name must already use the generated exact
pattern, stream scope, and supplied priority. The helper updates only
`max-age` and `max-length-bytes`, preserving the other definition keys. Set
segment size and initial replica count when declaring the stream with
`StreamQueueRequest`. Retention deletion works at segment granularity.
[RabbitMQ stream retention](https://www.rabbitmq.com/docs/streams#data-retention)

The helper stops if RabbitMQ reports another selected regular policy. RabbitMQ
applies one regular policy per stream. The highest priority wins, and matching
policies with equal priorities have an undefined winner. For an intentional
override, review the matching policies and use `client.policies.create()` with
a separate exact-name policy and a priority strictly above every matching
regular policy. Include the full definition the stream should retain, including
applicable keys from the previously selected policy. Leave shared policy
definitions in place. Coordinate other changes to these policy names and the
stream while writing because the broker's policy PUT replaces the definition
without a conditional update.
[RabbitMQ policy priorities](https://www.rabbitmq.com/docs/4.2/policies#policy-priorities)

Read the stream with `client.queues.get(stream_name)` after the write. Check both
`policy` and `effective_policy_definition`. An operator policy can affect the
effective values. The example waits briefly for the expected values and reports
the selected regular and operator policies if verification fails.
`QueueResponse.arguments` continues to show the original declaration.

On both pinned brokers, age accepts a positive integer followed by one
case-sensitive unit from `Y`, `M`, `D`, `h`, `m`, and `s`. Both the declaration
argument and the regular policy accept `7D` and reject `7d`.
[RabbitMQ 4.2.9 age parser](https://github.com/rabbitmq/rabbitmq-server/blob/v4.2.9/deps/rabbit/src/rabbit_amqqueue.erl#L990-L1006)
· [RabbitMQ 4.3.8 age parser](https://github.com/rabbitmq/rabbitmq-server/blob/v4.3.8/deps/rabbit/src/rabbit_amqqueue.erl#L1008-L1024)

## Clean up

The example deletes the stream it created with `client.queues.delete(stream_name)`
and separately removes its regular policy with `client.policies.delete(policy_name)`.
A policy is a virtual-host resource and survives stream deletion. If the example
is interrupted, inspect both names in the virtual host and remove the example
resources that still exist.
