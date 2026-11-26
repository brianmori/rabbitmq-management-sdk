# RabbitMQ 4.3.8 extreme topology

The SDK builds **5,000 resource nodes and 4,986 directed edges** from this
single-broker capture. RabbitMQ created the resources before the complete
cluster-wide export was taken. The broker had eight workload vhosts and the
default `/` vhost. This scenario measures SDK graph analysis with empty queues.

| Graph nodes | Count | Directed edges | Count |
| --- | ---: | --- | ---: |
| Queues (2,900 classic, 96 quorum, 64 stream) | 3,060 | Bindings | 4,442 |
| Exchanges | 1,927 | Dead-letter routes | 292 |
| Shovels | 13 | Alternate-exchange routes | 226 |
| | | Shovel endpoints | 26 |

RabbitMQ exports 1,926 exchanges, including its system log exchange. The SDK
adds the default exchange in `atlas.07` because a dead-letter route references
it. RabbitMQ creates implicit default-exchange bindings for queues. The SDK
edge count covers the routes represented in the topology graph.

## Scenarios

| Vhost or resource prefix | Workload |
| --- | --- |
| `atlas.00` to `atlas.07`, `tenant.*` | 192 tenant topologies with headers in all four `x-match` modes, topic and direct routing, parallel bindings, alternate exchanges, DLX routes, and parking queue limits |
| `atlas.00`, `00.dense.*` | Eight exchanges with 56 directed bindings yield 16,064 elementary cycles in one strongly connected component |
| Each workload vhost, `loop.*` | Binding self-loop and triangle, alternate pair, mixed binding and alternate loop, and three DLX loop variants |
| `geo.*`, `peer.*`, `relay.*` | Cross-vhost cycles spanning 4, 11, and 24 hops. One more cross-vhost shovel competes for a source queue |
| `atlas.04`, `deep.*` | 768-hop acyclic exchange route ending at `deep.end` |
| `atlas.05`, `hub.fanout` | 96 distinct direct queue targets with shared ingress |
| `atlas.06`, `ring.*` | 128-hop ring alternating queues and dead-letter exchanges |
| `atlas.07`, `special.*` | Bound examples of all six exchange types beyond direct, topic, fanout, and headers |
| `atlas.07`, `dangling.*` and `default.*` | Two absent destinations and one DLX route through the default exchange |

The broker registered ten exchange types and accepted declarations of all
ten. The six specialized types are `x-modulus-hash`, `x-local-random`,
`x-consistent-hash`, `x-jms-topic`, `x-random`, and `x-recent-history`.
The capture records configured routes and all 13 shovels reached the running
state. Keep the loop scenarios empty when measuring topology performance.

## Benchmark the saved capture

Run from the repository root after completing the
[development setup](../../../../CONTRIBUTING.md#local-development-setup).

```sh
uv run --python 3.12 python tests/integration/rmq-4.3/extreme-topology/benchmark.py
uv run --python 3.12 python tests/integration/rmq-4.3/extreme-topology/benchmark.py --max-cycles 0
```

The benchmark validates artifact hashes and graph results, then prints elapsed
times for file loading and graph construction, partitioning into strongly
connected components, and `audit()`. Pass `--runs` to set the number of trials
or `--definitions-only` to omit queue and exchange observations. The default
cap of 256 truncates the structural result while preserving all 28
message-loop candidates. The exhaustive run returns 16,124 structural cycles
across 61 cyclic components. The longest candidate has 128 hops.

| File | Capture |
| --- | --- |
| [full-export-definitions.json](full-export-definitions.json) | Full `GET /api/definitions` with `users` emptied to remove the disposable account hash. All topology sections and synthetic grants are preserved. |
| [queues.json](queues.json) | Complete `GET /api/queues?disable_stats=true` array, including 96 single-member quorum queues |
| [exchanges.json](exchanges.json) | Complete `GET /api/exchanges?disable_stats=true` array, including predeclared exchanges |
| [provenance.json](provenance.json) | Exact image ID, capture window, endpoints, resource counts, and artifact hashes |

The endpoints were read sequentially during one quiescent window under a
cluster administrator account. Both list requests used the broker's
unpaginated form. Shovel endpoint URIs are credential-free and point to vhosts
on the same broker. [RabbitMQ 4.3 dynamic shovels](https://www.rabbitmq.com/docs/4.3/shovel-dynamic).

## Provision and capture again

Run the commands below from the repository root using the locally available
4.3.8 image. Docker publishes management on a dynamically allocated loopback
port. This disposable broker uses its built-in guest account for management
and for localhost shovels. The image also enables Prometheus by default.

```sh
docker run --rm --pull=never -d \
  --name rabbitmq-sdk-extreme-438 --hostname rabbitmq-sdk-extreme-438 \
  --publish 127.0.0.1::15672 \
  --env 'RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS=-rabbit loopback_users []' \
  --entrypoint sh \
  sha256:fd4626e7c8ff066fc2ad8a608fd0364419ca919de00ddaca4bf1159f8350c1c8 \
  -ec 'rabbitmq-plugins enable --offline rabbitmq_management rabbitmq_shovel rabbitmq_shovel_management rabbitmq_consistent_hash_exchange rabbitmq_jms_topic_exchange rabbitmq_random_exchange rabbitmq_recent_history_exchange && exec docker-entrypoint.sh rabbitmq-server'

docker exec rabbitmq-sdk-extreme-438 rabbitmqctl await_startup
uv run --python 3.12 python tests/integration/rmq-4.3/extreme-topology/capture.py
```

[capture.py](capture.py) bulk-declares the vhosts, policies, exchanges, queues,
and bindings on the empty broker, then declares each shovel through the
Management API. After all shovels are running, it exports the full cluster,
prints the path to the byte-for-byte response in a private temporary
directory, and refreshes the sanitized bundle beside the script.
`--capture-only` refreshes the files from the existing benchmark broker.
Use `docker port rabbitmq-sdk-extreme-438 15672/tcp` to find its management port,
then `docker stop rabbitmq-sdk-extreme-438` when finished.
