# Disposable declaration and binding captures

These fixtures came from the repository's disposable Compose brokers running
RabbitMQ 4.2.9 and 4.3.8 in November 2026. Each file contains definitions and
paired Management API observations projected to one test vhost. The capture
renames the vhost and queue observation node, selects the wire fields used by
the replay, and drops cluster identifiers, users and all other vhosts. The
`observations` section holds results of the native AMQP declaration probe
while the exclusive queue's connection was open.

Both brokers accepted the exclusive non-durable classic declaration and
omitted it from the broker's queue definitions while it existed. The
non-exclusive declaration succeeded on 4.2.9 and failed on the default
4.3.8 setup with the `transient_nonexcl_queues` restriction. The captured
bindings were created and exported during the run. They provide no evidence
about restart behavior.

Regenerate in a temporary directory and review the projected JSON before
copying fixtures into the repository.

```sh
UV_PYTHON=3.12 \
  PYTEST_ADDOPTS=tests/live/test_declaration_compatibility.py \
  RABBIT_DECLARATION_EVIDENCE_DIR=/tmp/rabbitmq-declaration-evidence \
  just test-live
```

The Compose image tags follow the 4.2 and 4.3 patch lines. Captured broker
versions determine the output filenames.
