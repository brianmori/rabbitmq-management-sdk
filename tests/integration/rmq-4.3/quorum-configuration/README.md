# RabbitMQ 4.3 quorum configuration evidence

The live configuration tests run on **RabbitMQ 4.3.8** with
`just test-live-rmq43`. The checked-in JSON records configuration acceptance
and read-back. To refresh it, use the recapture command below.

## Saved fixture provenance

The saved responses came from RabbitMQ **4.3.6** on 2026-11-16, using the
disposable Compose broker and Python 3.12.13. They cover declaration and
read-back. Retry timing and disconnected consumers are outside this capture.

`configuration.json` contains projections of actual broker responses from:

- `GET /api/queues/{vhost}/configured` after an SDK quorum declaration with
  all five new queue arguments;
- `GET /api/policies/{vhost}/retry` after creating a regular quorum policy;
- `GET /api/definitions`, retaining the version and this test vhost's queue
  and policy entries.

The policy is selected by a second queue named `policy-queue`; the live test
waits for its broker-reported policy selection. It checks all four retry
selectors in both SDK serialization modes, plus operator-policy retry settings.
`configuration.json` represents the `all` selector in compatibility mode.
`operator-configuration.json` captures a queue argument, regular policy, and
operator policy conflict: effective retry type is `returned`, minimum 1000 ms,
maximum 8000 ms, and delivery limit 25. Both files include complete queue and
exchange observations projected to the isolated test vhost, with selected
policy names and `effective_policy_definition`. Runtime operator-policy
parameters for that vhost are retained in the definitions projection.

Sanitization replaces the random vhost with `captured` and the queue response's
node with `rabbit@captured-node`. Runtime statistics and unrelated queue response
fields are omitted. Member names are sanitized consistently for the disposable
single-node broker. The definitions projection excludes users, permissions,
non-operator runtime parameters, and other vhosts. No credentials or password hashes are
retained. Argument dictionaries and policy definitions are unchanged.

To recapture on the current disposable RabbitMQ 4.3.8 broker:

```bash
UV_PYTHON=3.12 \
RABBIT_QUORUM_CONFIG_EVIDENCE_DIR=/private/tmp/rmq-quorum-configuration \
just test-live-rmq43
```

Review the broker version and sanitization before replacing this evidence.
Ordinary offline and live tests do not regenerate checked-in captures.

See [configuration documentation](../../../../docs/quorum-queue-configuration.md)
for the versioned broker sources and the distinction between declaration and
policy validation.
