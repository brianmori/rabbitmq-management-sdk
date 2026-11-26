# RabbitMQ queue limits and retention captures

The live queue limits tests run on **RabbitMQ 4.2.9 and 4.3.8**. The recapture
command below uses those Compose targets.

## Saved fixture provenance

Captured on 2026-11-17 from the disposable Compose brokers with
the [test plugins](../../../rabbitmq-plugins) and [broker configuration](../../live/rabbitmq.conf).

| Source broker | Image digest |
| --- | --- |
| 4.2.9 | `rabbitmq@sha256:9e9fd21d60f060acc9ecbd847cd1dcb87e6550bdb0297c27e79a40bfe1766ce4` |
| 4.3.6 | `rabbitmq@sha256:8dd6e3570ddaa2ef82a6c3a8950e79c1f73893adfbdddbc374fc1f1ac8a0f5dd` |

The saved JSON projects the isolated test vhost from the unpaginated
`GET /api/definitions`, `GET /api/queues?disable_stats=true`, and
`GET /api/exchanges` responses. The requests used the disposable administrator
after provisioning had settled. The capture retains queue and exchange
declarations, binding records, scoped policies and runtime parameters, and
the corresponding queue and exchange observations. It omits user and
permission data, cluster and vhost metadata outside the projected identity,
stats, and membership details. Vhost names and broker node identities are
renamed consistently. The projection covers the experiment vhost.

- `orders` has a count argument of 10, regular policy of 3, and operator
  policy of 2. It has a message TTL of 60 seconds after policy resolution,
  with no unused-queue expiry. The live test also verifies drop-head behavior.
- `work` has independent length, byte and TTL settings, an unused-queue
  expiry, and a configured DLX. The overflow result shares the quorum
  delivery resolver's policy fallback.
- `history` has policy-derived 2-hour and 2,000,000-byte retention. The
  live test checks the resolved retention configuration in the broker.

The [capture replay](../test_queue_limits_captures.py) validates captured
configuration. The [live test](../../live/test_queue_limits.py) exercises
classic overflow and checks computed quorum and stream settings. Timing and
capacity estimates are outside the experiment.

To record a fresh projection from the repository root:

```bash
RABBIT_QUEUE_LIMITS_EVIDENCE_DIR=/tmp/rmq-queue-limits-evidence \
  PYTEST_ADDOPTS=tests/live/test_queue_limits.py UV_PYTHON=3.12 just test-live
```

Review the projection and update the versions and provenance deliberately.
