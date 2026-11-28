# Audit quorum resilience on RabbitMQ 4.2 and 4.3

An `orders` queue with three members needs two for a majority. If all three
are reported online, the observed failure margin is one. If two members share
a zone, losing that zone leaves one member. If just the leader is lost, two
members remain and the scenario separately records the loss of the captured
leader. Election completion, member connectivity, and current availability
require live evidence. See [RabbitMQ 4.2 quorum queues](https://www.rabbitmq.com/docs/4.2/quorum-queues#leader-election-and-failure-handling),
[RabbitMQ 4.3 quorum queues](https://www.rabbitmq.com/docs/4.3/quorum-queues#leader-election-and-failure-handling),
and [network partitions on 4.3](https://www.rabbitmq.com/docs/4.3/partitions).

## Capture and interpret the evidence

Save cluster-wide definitions and the complete queue and exchange observations
from one broker and capture window. Include the Management API fields
`members`, `leader`, and `online` in the queue observations. Their RabbitMQ
formatters use a view of running brokers for `online` and can report a
stored leader when the current leader lookup fails. The calculations use the
captured member list, which does not expose Raft voter transitions. See the
[capture contract](capture-contract.md),
[4.2.8 formatter](https://github.com/rabbitmq/rabbitmq-server/blob/v4.2.8/deps/rabbit/src/rabbit_quorum_queue.erl#L1864-L1900),
and [4.3.6 formatter](https://github.com/rabbitmq/rabbitmq-server/blob/v4.3.6/deps/rabbit/src/rabbit_quorum_queue.erl#L2174-L2209).

`QueueNode.quorum_placement` retains sorted member and online-member tuples
and the reported leader name. Omitted or null lists become `None`.
An empty member list preserves the empty capture and leaves
`replication_factor` unknown. An empty online list records zero reported
online members. An omitted, null, or empty leader leaves the identity
unknown. Placement survives `from_topology()` and trusted, compatible-version
pickle transfer. It is attached to the queue as metadata. The routing graph
still represents exchanges, queues, shovels, and message routes.

Call `ClusterAuditor.quorum_resilience()` for a frozen `QuorumResilienceReport`,
also available at `auditor.audit().quorum_resilience`. It has one `queues`
entry per declared quorum queue. Each entry exposes the complete queue node
in `queue` and calculates these fields from its evidence.

| Field | Meaning |
| --- | --- |
| `required_majority` | `N // 2 + 1` for a known group of `N` members. |
| `theoretical_failure_tolerance` | `N - required_majority`, assuming all members start online. Sizes 1 through 5 have tolerances 0, 0, 1, 1, and 2. |
| `observed_online_majority` | Whether the captured online count reaches the threshold, or `None` if either input is unknown. |
| `observed_failure_margin` | Reported online count minus the required majority. Zero means exactly enough, a negative value is the shortfall, and `None` means unknown. |

`brokers` lists known member placements and reported leader placements with
queue identities and counts. Counts can be partial when evidence is missing
for any queue. The optional `failure_domains` map uses exact broker node names
as keys and caller-defined zone, rack, or other shared-failure labels as values.
A broker without a map entry has `failure_domain=None`.

## Evaluate explicit failure scenarios

Pass one or more `QuorumFailureScenario` values to either facade method. Each
scenario removes the union of `failed_nodes` and brokers in `failed_domains`
from both the configured and the reported online groups. The original
majority threshold still applies. A failure here models loss of members,
and does not change the configured group or prescribe replica removal.

- `affected_queues` lists queues with a confirmed affected member, reported
  online member, or reported leader. The per-queue `affected` is `None` if
  the available evidence cannot settle whether any replica would be lost.
- `leader_failed` evaluates the reported leader independently of the
  majority, including when membership is unknown.
- `failed_members` retains confirmed losses among configured members.
  `unmapped_members` identifies configured members whose domains are needed
  and unknown. Both fields are `None` if configured member identities are
  unavailable.
- `member_majority_after_failure` uses the surviving configured members.
  `observed_online_majority_after_failure` and
  `observed_failure_margin_after_failure` use only the surviving reported
  online members. A calculation stays unknown if it depends on a missing
  domain or unavailable member data. Explicitly failed brokers need no
  domain assignment.

Queue results use canonical queue identity order and broker names are sorted
lexically. Equivalent scenarios are deduplicated and sorted by their
failed-node and failed-domain tuples.

Run this example from the repository root against the RabbitMQ 4.3.6 demo.
The caller assigns the demo's one broker to `demo-zone`. All three quorum
queues in the demo have a margin of zero and lose their majority in the
scenario.

```python
from pathlib import Path

from rabbitmq_management_sdk import ClusterAuditor
from rabbitmq_management_sdk.topology import QuorumFailureScenario

capture = Path("tests/integration/rmq-4.3/complex-topology")
auditor = ClusterAuditor.from_files(
    capture / "full-export-definitions.json",
    queues_path=capture / "queues.json",
    exchanges_path=capture / "exchanges.json",
)
report = auditor.quorum_resilience(
    failure_domains={"rabbit@sdk-topology-demo-43": "demo-zone"},
    scenarios=[QuorumFailureScenario(failed_domains=("demo-zone",))],
)
for result in report.queues:
    print(result.queue.id.name, result.required_majority, result.observed_failure_margin)
for impact in report.scenarios[0].queues:
    print(impact.queue.name, impact.member_majority_after_failure)
```

## Verification

Capture tests replay the RabbitMQ 4.2.8 queue listings with and without
statistics and the [RabbitMQ 4.3.6 topology demo](../tests/integration/rmq-4.3/complex-topology/README.md).
These are single-broker captures. Unit tests exercise one- through
five-member groups, correlated zone loss, leader loss, incomplete placement,
and trusted graph transfer using explicit multi-broker inputs. See the
[capture tests](../tests/integration/test_quorum_resilience_captures.py) and
[scenario tests](../tests/unit/test_quorum_resilience.py).
