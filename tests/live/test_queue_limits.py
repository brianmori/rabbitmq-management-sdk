"""Check limits and retention on a disposable RabbitMQ 4.2 or 4.3 broker."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

import pytest
from tests.shared.live_broker import wait_for

from rabbitmq_management_sdk import ClusterAuditor
from rabbitmq_management_sdk.topology import ConfigurationStatus

if TYPE_CHECKING:
    from tests.shared.live_broker import ManagementAPI

    from rabbitmq_management_sdk import RabbitMQClient

pytestmark = pytest.mark.live


def test_captured_effective_limits_and_retention(
    live_client: RabbitMQClient, live_vhost: str, management_api: ManagementAPI
) -> None:
    vhost = quote(live_vhost, safe="")
    for name, scope, regular, operator in (
        (
            "orders",
            "classic_queues",
            {"max-length": 3, "overflow": "reject-publish", "message-ttl": 60_000},
            {"max-length": 2, "max-length-bytes": 2048},
        ),
        (
            "work",
            "quorum_queues",
            {"max-length": 5, "overflow": "reject-publish-dlx", "dead-letter-exchange": "errors"},
            {"message-ttl": 50_000},
        ),
        (
            "history",
            "streams",
            {"max-age": "2h", "max-length-bytes": 4_000_000},
            {"max-length-bytes": 2_000_000},
        ),
    ):
        for endpoint, definition in (("policies", regular), ("operator-policies", operator)):
            management_api.request(
                "PUT",
                f"{endpoint}/{vhost}/{name}-{endpoint}",
                {"pattern": f"^{name}$", "apply-to": scope, "priority": 1, "definition": definition},
            )
    management_api.request("PUT", f"exchanges/{vhost}/errors", {"type": "fanout", "durable": True})
    for name, arguments in (
        (
            "orders",
            {
                "x-queue-type": "classic",
                "x-max-length": 10,
                "x-max-length-bytes": 4096,
                "x-overflow": "drop-head",
                "x-message-ttl": 90_000,
            },
        ),
        (
            "work",
            {
                "x-queue-type": "quorum",
                "x-max-length": 10,
                "x-max-length-bytes": 8192,
                "x-overflow": "reject-publish",
                "x-message-ttl": 80_000,
                "x-expires": 600_000,
            },
        ),
        ("history", {"x-queue-type": "stream", "x-max-age": "30m", "x-max-length-bytes": 1_000_000}),
    ):
        management_api.request("PUT", f"queues/{vhost}/{name}", {"durable": True, "arguments": arguments})

    wait_for(
        lambda: live_client.queues.get("history").operator_policy == "history-operator-policies",
        "stream operator policy selection",
    )
    auditor = ClusterAuditor(
        live_client.admin.export_definitions(),
        queues=live_client.queues.list_all(disable_stats=True),
        exchanges=live_client.exchanges.list_all(disable_stats=True),
    )
    results = {row.queue.name: row for row in auditor.queue_limits() if row.queue.vhost == live_vhost}
    orders = results["orders"].configuration
    assert orders.max_length.value == 2
    assert orders.max_length_bytes.value == 2048
    assert orders.overflow.value == "drop-head"
    assert orders.message_ttl_ms.value == 60_000
    assert orders.queue_expiry_ms.status == ConfigurationStatus.UNSET
    for payload in ("one", "two", "three"):
        assert management_api.publish(live_vhost, "amq.default", "orders", payload)
    assert [message["payload"] for message in management_api.messages(live_vhost, "orders")] == ["two", "three"]

    work = results["work"]
    assert work.configuration.max_length.value == 5
    assert work.configuration.max_length_bytes.value == 8192
    assert work.configuration.message_ttl_ms.value == 50_000
    assert work.configuration.queue_expiry_ms.value == 600_000
    quorum = next(item.configuration for item in auditor.quorum_delivery_configuration() if item.queue == work.queue)
    assert work.configuration.overflow is quorum.overflow
    assert quorum.overflow.value == "drop-head"
    assert work.dead_letter_coverage.dead_letter_exchange is not None
    assert work.dead_letter_coverage.dead_letter_exchange.name == "errors"

    history = results["history"].configuration
    assert history.stream_max_age_ms.value == 7_200_000
    assert history.stream_max_length_bytes.value == 2_000_000
    assert history.overflow.status == ConfigurationStatus.NOT_APPLICABLE
    _check_broker_configuration(live_vhost)
    _capture_limits(management_api, live_vhost)


def _check_broker_configuration(vhost: str) -> None:
    container = os.getenv("RABBIT_TEST_CONTAINER")
    if container is None:
        return
    expression = (
        f"Vhost = iolist_to_binary({json.dumps(vhost)}), "
        '{ok, Stream} = rabbit_amqqueue:lookup(rabbit_misc:r(Vhost, queue, <<"history">>)), '
        "Conf = rabbit_stream_queue:update_stream_conf(Stream, #{}), "
        "[{max_age,7200000},{max_bytes,2000000}] = lists:sort(maps:get(retention, Conf)), "
        '{ok, Quorum} = rabbit_amqqueue:lookup(rabbit_misc:r(Vhost, queue, <<"work">>)), '
        "{ok, {_, Overview}, _} = ra:local_query(amqqueue:get_pid(Quorum), fun rabbit_fifo:overview/1), "
        "#{max_length := 5, max_bytes := 8192, msg_ttl := 50000, expires := 600000, "
        "overflow_strategy := drop_head, dead_lettering_enabled := true} = maps:get(config, Overview), "
        "true."
    )
    completed = subprocess.run(
        ["docker", "exec", container, "rabbitmqctl", "-q", "eval", expression],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0 and completed.stdout.strip().splitlines()[-1:] == ["true"], completed.stderr


def _capture_limits(api: ManagementAPI, vhost: str) -> None:
    output = os.getenv("RABBIT_QUEUE_LIMITS_EVIDENCE_DIR")
    if output is None:
        return
    definitions = api.request("GET", "definitions")
    queues = api.request("GET", "queues?disable_stats=true")
    exchanges = api.request("GET", "exchanges")
    assert isinstance(definitions, dict) and isinstance(queues, list) and isinstance(exchanges, list)
    projection = {
        key: [{**item, "vhost": "captured"} for item in definitions[key] if item.get("vhost") == vhost]
        for key in ("queues", "policies", "parameters", "exchanges", "bindings")
    }
    queue_keys = {
        "name",
        "vhost",
        "durable",
        "auto_delete",
        "exclusive",
        "type",
        "node",
        "state",
        "arguments",
        "policy",
        "operator_policy",
        "effective_policy_definition",
    }
    exchange_keys = {"name", "vhost", "type", "durable", "auto_delete", "internal", "arguments", "policy"}
    version = definitions["rabbitmq_version"]
    data = {
        "definitions": {**projection, "rabbitmq_version": version},
        "queues": [
            {**{k: row[k] for k in sorted(queue_keys & row.keys())}, "vhost": "captured", "node": "rabbit@captured"}
            for row in queues
            if row["vhost"] == vhost
        ],
        "exchanges": [
            {**{k: row[k] for k in sorted(exchange_keys & row.keys())}, "vhost": "captured"}
            for row in exchanges
            if row["vhost"] == vhost
        ],
    }
    path = Path(output)
    path.mkdir(parents=True, exist_ok=True)
    (path / f"rmq-{version}.json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
