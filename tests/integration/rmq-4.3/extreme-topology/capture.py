"""Provision and capture the extreme topology on the isolated 4.3.8 broker."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import subprocess
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime
from http.client import HTTPConnection
from itertools import pairwise, permutations
from pathlib import Path
from typing import Any
from urllib.parse import quote

IMAGE = "sha256:fd4626e7c8ff066fc2ad8a608fd0364419ca919de00ddaca4bf1159f8350c1c8"
VERSION = "4.3.8"
CONTAINER = "rabbitmq-sdk-extreme-438"
DIRECTORY = Path(__file__).resolve().parent
REGIONS = tuple(f"atlas.{index:02d}" for index in range(8))
TENANTS = range(24)
TARGET_NODES = 5_000

type Record = dict[str, Any]


class Scenario:
    def __init__(self) -> None:
        self.queues: list[Record] = []
        self.exchanges: list[Record] = []
        self.bindings: list[Record] = []
        self.shovels: list[Record] = []

    def exchange(self, vhost: str, name: str, kind: str = "fanout", **arguments: object) -> str:
        self.exchanges.append(
            {
                "vhost": vhost,
                "name": name,
                "type": kind,
                "durable": True,
                "auto_delete": False,
                "internal": False,
                "arguments": arguments,
            }
        )
        return name

    def queue(self, vhost: str, name: str, kind: str = "classic", **arguments: object) -> str:
        if kind == "quorum":
            arguments["x-quorum-initial-group-size"] = 1
        self.queues.append(
            {
                "vhost": vhost,
                "name": name,
                "durable": True,
                "auto_delete": False,
                "arguments": {"x-queue-type": kind, **arguments},
            }
        )
        return name

    def bind(
        self,
        vhost: str,
        source: str,
        destination: str,
        kind: str = "queue",
        key: str = "",
        *,
        arguments: dict[str, object] | None = None,
    ) -> None:
        self.bindings.append(
            {
                "vhost": vhost,
                "source": source,
                "destination": destination,
                "destination_type": kind,
                "routing_key": key,
                "arguments": arguments or {},
            }
        )

    def shovel(
        self,
        name: str,
        source_vhost: str,
        source: str,
        destination_vhost: str,
        destination: str,
        *,
        destination_kind: str = "exchange",
    ) -> None:
        self.shovels.append(
            {
                "vhost": REGIONS[0],
                "component": "shovel",
                "name": name,
                "value": {
                    "src-uri": f"amqp:///{source_vhost}",
                    "src-queue": source,
                    "dest-uri": f"amqp:///{destination_vhost}",
                    f"dest-{destination_kind}": destination,
                    "src-predeclared": True,
                    "dest-predeclared": True,
                    "ack-mode": "on-confirm",
                },
            }
        )


def tenant_routes(scenario: Scenario, vhost: str, tenant: int) -> None:
    prefix = f"tenant.{tenant:02d}"
    park = scenario.exchange(vhost, f"{prefix}.park")
    entry = scenario.exchange(vhost, f"{prefix}.ingress", "topic", **{"alternate-exchange": park})
    headers = scenario.exchange(vhost, f"{prefix}.headers", "headers")
    broadcast = scenario.exchange(vhost, f"{prefix}.broadcast")
    work = scenario.exchange(vhost, f"{prefix}.work", "direct")
    scenario.bind(vhost, entry, headers, "exchange", "orders.*")
    scenario.bind(vhost, entry, broadcast, "exchange", "events.#")
    scenario.bind(vhost, entry, work, "exchange", "job")
    queues = []
    for slot in range(15):
        kind = "classic"
        if slot == 8 and tenant % 3 == 0:
            kind = "stream"
        if slot == 11 and tenant % 2 == 0:
            kind = "quorum"
        arguments = {"x-dead-letter-exchange": park} if slot == 11 else {}
        queues.append(scenario.queue(vhost, f"{prefix}.q{slot:02d}", kind, **arguments))

    for index, mode in enumerate(("all", "any", "all-with-x", "any-with-x")):
        scenario.bind(vhost, headers, queues[index], arguments={"x-match": mode, "region": vhost, "x-lane": index})
    for queue in queues[4:9]:
        scenario.bind(vhost, broadcast, queue)
    if tenant < 12:
        scenario.bind(vhost, broadcast, queues[4], key="parallel")
    for queue in queues[9:13]:
        scenario.bind(vhost, work, queue, key="job")
    scenario.bind(vhost, park, queues[13])


def regional_cycles(scenario: Scenario, vhost: str) -> None:
    self_loop = scenario.exchange(vhost, "loop.binding.self")
    scenario.bind(vhost, self_loop, self_loop, "exchange")
    first = scenario.exchange(vhost, "loop.binding.a", "direct")
    second = scenario.exchange(vhost, "loop.binding.b", "topic")
    third = scenario.exchange(vhost, "loop.binding.c")
    for target, source, key in ((second, first, "loop"), (third, second, "#"), (first, third, "")):
        scenario.bind(vhost, source, target, "exchange", key)
    scenario.bind(vhost, first, second, "exchange", "parallel")

    for source, target in (("a", "b"), ("b", "a")):
        scenario.exchange(vhost, f"loop.alternate.{source}", **{"alternate-exchange": f"loop.alternate.{target}"})
    mixed = scenario.exchange(vhost, "loop.mixed.a", **{"alternate-exchange": "loop.mixed.b"})
    relay = scenario.exchange(vhost, "loop.mixed.b")
    scenario.bind(vhost, relay, mixed, "exchange")

    direct_dlx = scenario.exchange(vhost, "loop.dlx.self")
    queue = scenario.queue(vhost, "loop.dlx.self", **{"x-dead-letter-exchange": direct_dlx})
    scenario.bind(vhost, direct_dlx, queue)
    for source, target in (("a", "b"), ("b", "a")):
        exchange = scenario.exchange(vhost, f"loop.dlx.{source}")
        queue = scenario.queue(vhost, f"loop.dlx.{source}", **{"x-dead-letter-exchange": f"loop.dlx.{target}"})
        scenario.bind(vhost, exchange, queue)

    fallback = scenario.exchange(vhost, "loop.dlx-ae.fallback")
    entry = scenario.exchange(vhost, "loop.dlx-ae.entry", **{"alternate-exchange": fallback})
    queue = scenario.queue(vhost, "loop.dlx-ae.queue", **{"x-dead-letter-exchange": entry})
    scenario.bind(vhost, fallback, queue)


def cross_vhost_cycles(scenario: Scenario) -> None:
    for vhost, next_vhost in zip(REGIONS, (*REGIONS[1:], REGIONS[0]), strict=True):
        scenario.exchange(vhost, "geo.entry")
        scenario.queue(vhost, "geo.out")
        scenario.bind(vhost, "geo.entry", "geo.out")
        scenario.shovel(f"geo.{vhost}", vhost, "geo.out", next_vhost, "geo.entry")

    west, east = REGIONS[:2]
    for vhost, next_vhost in ((west, east), (east, west)):
        scenario.queue(vhost, "geo.peer")
        scenario.shovel(f"peer.{vhost}", vhost, "geo.peer", next_vhost, "geo.peer", destination_kind="queue")

    for vhost, next_vhost in ((west, east), (east, west)):
        entry = "relay.entry"
        if vhost == east:
            fallback = scenario.exchange(vhost, "relay.fallback")
            scenario.exchange(vhost, entry, **{"alternate-exchange": fallback})
            entry = fallback
        else:
            scenario.exchange(vhost, entry)
        dlx = scenario.exchange(vhost, "relay.dlx")
        work = scenario.queue(vhost, "relay.work", **{"x-dead-letter-exchange": dlx})
        outbound = scenario.queue(vhost, "relay.out")
        scenario.bind(vhost, entry, work)
        scenario.bind(vhost, dlx, outbound)
        scenario.shovel(f"relay.{vhost}", vhost, outbound, next_vhost, "relay.entry")

    scenario.shovel("compete.geo", west, "geo.out", REGIONS[3], "tenant.00.q14", destination_kind="queue")


def deep_routes(scenario: Scenario) -> None:
    vhost = REGIONS[4]
    chain = [scenario.exchange(vhost, f"deep.{index:03d}") for index in range(768)]
    for source, target in pairwise(chain):
        scenario.bind(vhost, source, target, "exchange")
    scenario.bind(vhost, chain[-1], scenario.queue(vhost, "deep.end"))

    vhost = REGIONS[6]
    for index in range(64):
        next_index = (index + 1) % 64
        exchange = scenario.exchange(vhost, f"ring.exchange.{index:02d}")
        queue = scenario.queue(
            vhost,
            f"ring.queue.{index:02d}",
            **{
                "x-dead-letter-exchange": f"ring.exchange.{next_index:02d}",
            },
        )
        scenario.bind(vhost, exchange, queue)

    dense = [scenario.exchange(REGIONS[0], f"00.dense.{index}") for index in range(8)]
    for source, target in permutations(dense, 2):
        scenario.bind(REGIONS[0], source, target, "exchange")


def exchange_types_and_diagnostics(scenario: Scenario) -> None:
    vhost = REGIONS[-1]
    kinds = ("x-modulus-hash", "x-local-random", "x-jms-topic", "x-consistent-hash", "x-random", "x-recent-history")
    for kind in kinds:
        exchange = scenario.exchange(vhost, f"special.{kind}", kind)
        queue = scenario.queue(vhost, f"special.{kind}.sink")
        key = "1" if kind == "x-consistent-hash" else ""
        scenario.bind(
            vhost,
            exchange,
            queue,
            key=key,
            arguments={"rjms_erlang_selector": "true."} if kind == "x-jms-topic" else None,
        )

    scenario.queue(vhost, "default.dlx", **{"x-dead-letter-exchange": "", "x-dead-letter-routing-key": "default.sink"})
    scenario.queue(vhost, "default.sink")
    scenario.queue(vhost, "dangling.dlx", **{"x-dead-letter-exchange": "absent.dlx"})
    scenario.exchange(vhost, "dangling.ae", **{"alternate-exchange": "absent.ae"})

    vhost = REGIONS[5]
    hub = scenario.exchange(vhost, "hub.fanout")
    for tenant in TENANTS:
        for slot in (2, 6, 10, 14):
            scenario.bind(vhost, hub, f"tenant.{tenant:02d}.q{slot:02d}")


def scenario() -> Scenario:
    result = Scenario()
    for vhost in REGIONS:
        for tenant in TENANTS:
            tenant_routes(result, vhost, tenant)
        regional_cycles(result, vhost)
    cross_vhost_cycles(result)
    deep_routes(result)
    exchange_types_and_diagnostics(result)
    # The broker exports the system log exchange; the parser adds the referenced default exchange.
    reserved_nodes = 2
    declared_nodes = len(result.queues) + len(result.exchanges) + len(result.shovels)
    for index in range(TARGET_NODES - declared_nodes - reserved_nodes):
        result.queue(REGIONS[index % len(REGIONS)], f"unattached.{index:03d}")
    return result


def docker_inspect(container: str) -> Record:
    inspection: Record = json.loads(subprocess.check_output(["docker", "inspect", "--type", "container", container]))[0]
    return inspection


def management(port: int, path: str, *, method: str = "GET", body: object = None) -> bytes:
    connection = HTTPConnection("127.0.0.1", port, timeout=120)
    headers = {
        "Authorization": "Basic " + base64.b64encode(b"guest:guest").decode("ascii"),
        "Content-Type": "application/json",
    }
    try:
        connection.request(method, f"/api/{path}", None if body is None else json.dumps(body), headers)
        response = connection.getresponse()
        payload = response.read()
        if not 200 <= response.status < 300:
            detail = payload[:500].decode(errors="replace") if method != "GET" else response.reason
            raise RuntimeError(f"{method} /api/{path} returned {response.status}: {detail}")
        return payload
    finally:
        connection.close()


def provision(port: int, planned: Scenario) -> None:
    before = json.loads(management(port, "definitions"))
    if (
        [vhost["name"] for vhost in before["vhosts"]] != ["/"]
        or {item["name"] for item in before["exchanges"]} != {"amq.rabbitmq.log"}
        or any(before[section] for section in ("queues", "bindings", "parameters", "policies"))
    ):
        raise RuntimeError("Provisioning requires a fresh benchmark container. Use --capture-only to recapture.")
    declarations = {
        "vhosts": [{"name": vhost, "metadata": {"default_queue_type": "classic"}} for vhost in REGIONS],
        "permissions": [
            {"user": "guest", "vhost": vhost, "configure": ".*", "write": ".*", "read": ".*"} for vhost in REGIONS
        ],
        "policies": [
            {
                "vhost": vhost,
                "name": "parking-cap",
                "pattern": r"^tenant\.[0-9]+\.q13$",
                "apply-to": "queues",
                "priority": 0,
                "definition": {"max-length": 10_000},
            }
            for vhost in REGIONS
        ],
        "exchanges": planned.exchanges,
        "queues": planned.queues,
        "bindings": planned.bindings,
    }
    management(port, "definitions", method="POST", body=declarations)
    for shovel in planned.shovels:
        vhost = quote(shovel["vhost"], safe="")
        name = quote(shovel["name"], safe="")
        management(port, f"parameters/shovel/{vhost}/{name}", method="PUT", body={"value": shovel["value"]})


def running_shovels(port: int, planned: Scenario) -> None:
    expected = {(shovel["vhost"], shovel["name"]) for shovel in planned.shovels}
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        statuses = json.loads(management(port, "shovels"))
        running = {(item["vhost"], item["name"]) for item in statuses if item["state"] == "running"}
        if running == expected:
            return
        time.sleep(1)
    raise RuntimeError(f"Shovels did not reach running state: {sorted(expected - running)}")


def capture(port: int, planned: Scenario, inspection: Record) -> None:
    running_shovels(port, planned)
    start = datetime.now(UTC)
    definitions_bytes = management(port, "definitions")
    queue_bytes = management(port, "queues?disable_stats=true")
    exchange_bytes = management(port, "exchanges?disable_stats=true")
    end = datetime.now(UTC)
    definitions = json.loads(definitions_bytes)
    queues = json.loads(queue_bytes)
    exchanges = json.loads(exchange_bytes)
    if definitions["rabbitmq_version"] != VERSION:
        raise RuntimeError("The saved definitions came from a different broker version")
    if not isinstance(queues, list) or not isinstance(exchanges, list):
        raise RuntimeError("Expected complete, unpaginated observation arrays")
    queue_ids = {(queue["vhost"], queue["name"]) for queue in planned.queues}
    exchange_ids = {(exchange["vhost"], exchange["name"]) for exchange in planned.exchanges}
    if queue_ids != {(item["vhost"], item["name"]) for item in queues}:
        raise RuntimeError("The queue observations differ from the provisioned identities")
    if not exchange_ids <= {(item["vhost"], item["name"]) for item in exchanges}:
        raise RuntimeError("The exchange observations are missing provisioned identities")
    if len(definitions["bindings"]) != len(planned.bindings):
        raise RuntimeError("The binding export differs from the provisioned count")

    raw_path = Path(tempfile.mkdtemp(prefix="rabbitmq-sdk-extreme-438-")) / "full-export-definitions.json"
    raw_path.touch(mode=0o600)
    raw_path.write_bytes(definitions_bytes)
    print(f"Original full export: {raw_path}")

    # Keep every topology declaration and the synthetic grants. Remove the disposable account and its hash.
    definitions["users"] = []
    artifacts = {
        "full-export-definitions.json": (json.dumps(definitions, separators=(",", ":")) + "\n").encode(),
        "queues.json": queue_bytes,
        "exchanges.json": exchange_bytes,
    }
    for filename, contents in artifacts.items():
        (DIRECTORY / filename).write_bytes(contents)
    provenance = {
        "broker_version": VERSION,
        "docker_image_id": inspection["Image"],
        "window_utc": [start.isoformat(), end.isoformat()],
        "endpoints": ["/api/definitions", "/api/queues?disable_stats=true", "/api/exchanges?disable_stats=true"],
        "scope": "Cluster-wide. Complete, unpaginated observation arrays. No messages published.",
        "sanitization": "Emptied users to remove the disposable account and hash. Kept synthetic guest grants.",
        "counts": {
            **{key: len(definitions[key]) for key in ("vhosts", "queues", "exchanges", "bindings", "policies")},
            "shovels_running": len(planned.shovels),
            "observed_exchanges": len(exchanges),
        },
        "queue_types": dict(sorted(Counter(queue["type"] for queue in queues).items())),
        "exchange_types": sorted({exchange["type"] for exchange in definitions["exchanges"]}),
        "original_definitions_sha256": hashlib.sha256(definitions_bytes).hexdigest(),
        "sha256": {key: hashlib.sha256(value).hexdigest() for key, value in artifacts.items()},
    }
    (DIRECTORY / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(provenance["counts"], sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", default=CONTAINER)
    parser.add_argument("--capture-only", action="store_true", help="Export an already provisioned benchmark broker.")
    options = parser.parse_args()
    inspection = docker_inspect(options.container)
    bindings = inspection["NetworkSettings"]["Ports"].get("15672/tcp") or []
    if inspection["Image"] != IMAGE or len(bindings) != 1 or bindings[0]["HostIp"] != "127.0.0.1":
        raise RuntimeError("Expected the pinned 4.3.8 image and a single localhost-only management port")
    port = int(bindings[0]["HostPort"])
    overview = json.loads(management(port, "overview"))
    if overview["rabbitmq_version"] != VERSION:
        raise RuntimeError("The running broker version is not 4.3.8")
    planned = scenario()
    registered = {entry["name"] for entry in overview["exchange_types"]}
    required = {entry["type"] for entry in planned.exchanges}
    if registered != required:
        raise RuntimeError(f"Exchange type mismatch: missing {required - registered}, unused {registered - required}")
    print(f"RabbitMQ {VERSION} management at http://127.0.0.1:{port}", flush=True)
    if not options.capture_only:
        provision(port, planned)
    capture(port, planned, inspection)


if __name__ == "__main__":
    main()
