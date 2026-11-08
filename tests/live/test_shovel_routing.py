"""Reproduce directional shovel keys and actual deliveries on disposable brokers.

Legacy address tests require deprecated_features.permit.amqp_address_v1=true.
The Compose brokers load this from tests/live/rabbitmq.conf. Set
RABBIT_SHOVEL_EVIDENCE_DIR to save credential-free, projected evidence.
The live recipe supplies RABBIT_TEST_CONTAINER for the native subject publisher.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast
from urllib.parse import quote

import pytest
from tests.shared.live_broker import wait_for

from rabbitmq_management_sdk import ClusterAuditor, RabbitMQClient
from rabbitmq_management_sdk.topology import NodeKind

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.conftest import RabbitSettings
    from tests.shared.live_broker import ManagementAPI

    from rabbitmq_management_sdk.topology import ShovelNode

pytestmark = pytest.mark.live


@dataclass
class _Broker:
    client: RabbitMQClient = field(repr=False)
    http: ManagementAPI = field(repr=False)
    vhost: str
    uri091: str = field(repr=False)
    uri_local: str = field(repr=False)
    uri10: str = field(repr=False)

    def api(self, method: str, path: str, body: dict[str, Any] | None = None) -> object:
        return self.http.request(method, path, body)

    def objects(self, path: str) -> list[dict[str, Any]]:
        return cast("list[dict[str, Any]]", self.api("GET", path))

    def queue(self, name: str) -> None:
        self.api(
            "PUT",
            f"queues/{self.vhost}/{name}",
            {"durable": True, "auto_delete": False, "arguments": {"x-queue-type": "classic"}},
        )

    def exchange(self, name: str) -> None:
        self.api(
            "PUT",
            f"exchanges/{self.vhost}/{name}",
            {"type": "direct", "durable": True, "auto_delete": False, "internal": False, "arguments": {}},
        )

    def bind(self, queue: str, key: str) -> None:
        self.api("POST", f"bindings/{self.vhost}/e/output/q/{queue}", {"routing_key": key, "arguments": {}})

    def start(self, value: dict[str, Any]) -> None:
        # Raw broker parameters also exercise omitted source keys, which the
        # request constructor deliberately requires for exchange sources.
        self.api(
            "PUT",
            f"parameters/shovel/{self.vhost}/s",
            {
                "value": value,
            },
        )

    def status(self) -> dict[str, Any]:
        return next((status for status in self.objects(f"shovels/{self.vhost}") if status["name"] == "s"), {})

    def wait[T](self, callback: Callable[[], T], label: str) -> T:
        return wait_for(callback, label)

    def running(self) -> None:
        self.wait(lambda: self.status().get("state") in ("running", "flow"), "shovel to run")

    def publish(self, exchange: str, key: str, payload: str = "delivery-proof") -> bool:
        return self.http.publish(self.vhost, exchange, key, payload)

    def messages(self, queue: str) -> list[dict[str, Any]]:
        return self.http.messages(self.vhost, queue)

    def publish_native_subject(self) -> None:
        container = os.getenv("RABBIT_TEST_CONTAINER")
        if container is None:
            pytest.skip("native AMQP 1.0 publisher requires the disposable Compose live-test recipe")
        script = Path(__file__).with_name("publish_amqp10_subject.erl").read_text(encoding="utf-8")
        result = subprocess.run(
            ["docker", "exec", container, "rabbitmqctl", "-q", "eval", f"Vhost = {json.dumps(self.vhost)}, " + script],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        last_line = result.stdout.strip().splitlines()[-1:]
        assert result.returncode == 0 and last_line == ["published"], "native subject publisher failed"

    def assert_endpoint(self, side: Literal["src", "dest"], kind: NodeKind | None, key: str | None) -> ShovelNode:
        auditor = ClusterAuditor(self.client.admin.export_definitions(), in_cluster_amqp_hosts={"localhost"})
        shovel = next(node for node in auditor.topology.shovels if node.id.vhost == self.vhost and node.id.name == "s")
        endpoint = shovel.source if side == "src" else shovel.destination
        assert endpoint.resource_kind == kind
        assert endpoint.routing_key == key
        assert endpoint.vhost == self.vhost
        assert endpoint.is_confirmed_local is True
        edges = [
            edge for edge in auditor.topology.edges if (edge.target if side == "src" else edge.source) == shovel.id
        ]
        if kind is None:
            assert edges == []
            assert endpoint.resource_name is None
        else:
            (edge,) = edges
            assert edge.routing_key == key
            assert (edge.source if side == "src" else edge.target).kind == kind
        return shovel

    def record(self, case: str, observations: dict[str, object]) -> None:
        output = os.getenv("RABBIT_SHOVEL_EVIDENCE_DIR")
        if output is None:
            return
        definitions = self.client.admin.export_definitions()
        parameter = next(p for p in definitions.parameters if p.vhost == self.vhost and p.name == "s")
        assert isinstance(parameter.value, dict)
        # Project only resource/key fields from the captured parameter. Raw URI
        # credentials and runtime failure reasons are never written to evidence.
        value = {
            key: item
            for key, item in parameter.value.items()
            if key
            in {
                "src-protocol",
                "src-queue",
                "src-exchange",
                "src-exchange-key",
                "src-address",
                "dest-protocol",
                "dest-queue",
                "dest-exchange",
                "dest-exchange-key",
                "dest-address",
            }
        }
        for side in ("src", "dest"):
            value[f"{side}-uri"] = (
                "amqp://localhost?hostname=vhost:captured"
                if value.get(f"{side}-protocol") == "amqp10"
                else "amqp:///captured"
            )
        path = Path(output) / str(self.client.version)
        path.mkdir(parents=True, exist_ok=True)
        (path / f"{case}.json").write_text(
            json.dumps(
                {
                    "broker_version": str(self.client.version),
                    "parameter": {"vhost": "captured", "component": "shovel", "name": case, "value": value},
                    "observations": observations,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )


@pytest.fixture
def broker(
    rabbitmq_client_compatibility: RabbitMQClient,
    rabbit_config: RabbitSettings,
    management_api: ManagementAPI,
    live_vhost: str,
) -> _Broker:
    userinfo = f"{quote(rabbit_config.username, safe='')}:{quote(rabbit_config.password, safe='')}"
    return _Broker(
        rabbitmq_client_compatibility,
        management_api,
        live_vhost,
        f"amqp://{userinfo}@/{live_vhost}",
        f"amqp://{userinfo}@/{live_vhost}",
        f"amqp://{userinfo}@localhost:5672?hostname=vhost:{live_vhost}&sasl=plain",
    )


@pytest.mark.parametrize("protocol", ["amqp091", "local"])
@pytest.mark.parametrize("key", [None, "", "fixed.key"], ids=["omitted", "empty", "nonempty"])
def test_source_exchange_binding_and_delivery(broker: _Broker, protocol: str, key: str | None) -> None:
    broker.exchange("events")
    broker.queue("sink")
    value = {
        "src-protocol": protocol,
        "src-uri": broker.uri_local if protocol == "local" else broker.uri091,
        "src-exchange": "events",
        "dest-protocol": "amqp091",
        "dest-uri": broker.uri091,
        "dest-queue": "sink",
    }
    if key is not None:
        value["src-exchange-key"] = key
    broker.start(value)
    broker.running()
    bindings = broker.wait(lambda: broker.objects(f"exchanges/{broker.vhost}/events/bindings/source"), "source binding")
    (binding,) = bindings
    generated_key = key in (None, "")
    if generated_key:
        assert binding["routing_key"] != ""
        assert binding["routing_key"] == binding["destination"]
        assert broker.publish("events", "", "must-not-arrive") is False
    else:
        assert binding["routing_key"] == key
    assert broker.publish("events", binding["routing_key"])
    (message,) = broker.wait(lambda: broker.messages("sink"), "source delivery")
    assert message["payload"] == "delivery-proof"
    broker.assert_endpoint("src", NodeKind.EXCHANGE, None if generated_key else key)
    broker.record(
        f"{protocol}-source-{'omitted' if key is None else 'empty' if key == '' else 'nonempty'}",
        {
            "binding_key_is_generated_queue": generated_key,
            "binding_key_is_empty": binding["routing_key"] == "",
            "payload_delivered": message["payload"],
        },
    )


@pytest.mark.parametrize("protocol", ["amqp091", "local"])
@pytest.mark.parametrize("key", [None, "", "fixed.key"], ids=["omitted", "empty", "nonempty"])
def test_destination_exchange_key_and_delivery(broker: _Broker, protocol: str, key: str | None) -> None:
    broker.queue("input")
    broker.exchange("output")
    broker.queue("sink")
    expected_delivery_key = "input" if key is None else key
    broker.bind("sink", expected_delivery_key)
    value = {
        "src-protocol": "amqp091",
        "src-uri": broker.uri091,
        "src-queue": "input",
        "dest-protocol": protocol,
        "dest-uri": broker.uri_local if protocol == "local" else broker.uri091,
        "dest-exchange": "output",
    }
    if key is not None:
        value["dest-exchange-key"] = key
    broker.start(value)
    broker.running()
    assert broker.publish("amq.default", "input")
    (message,) = broker.wait(lambda: broker.messages("sink"), "destination delivery")
    assert message["payload"] == "delivery-proof"
    assert message["routing_key"] == expected_delivery_key
    broker.assert_endpoint("dest", NodeKind.EXCHANGE, key)
    broker.record(
        f"{protocol}-destination-{'omitted' if key is None else 'empty' if key == '' else 'nonempty'}",
        {"payload_delivered": message["payload"], "delivered_key": message["routing_key"]},
    )


@pytest.mark.parametrize("address", ["/exchanges/events", "/exchanges/events/", "/exchanges/events/fixed"])
def test_v2_exchange_source_is_rejected_without_a_topology_edge(broker: _Broker, address: str) -> None:
    broker.exchange("events")
    broker.queue("sink")
    broker.start(
        {
            "src-protocol": "amqp10",
            "src-uri": broker.uri10,
            "src-address": address,
            "dest-protocol": "amqp091",
            "dest-uri": broker.uri091,
            "dest-queue": "sink",
            "reconnect-delay": 1,
        }
    )

    def rejected() -> bool:
        rendered = json.dumps(broker.status())
        return "bad_address" in rendered or "invalid-field" in rendered

    broker.wait(rejected, "v2 source rejection")
    shovel = broker.assert_endpoint("src", None, None)
    assert shovel.source.authorities is not None
    assert shovel.source.authorities[0].host == "localhost"
    broker.record(
        f"amqp10-source-{'nonempty' if address.endswith('fixed') else 'empty' if address.endswith('/') else 'omitted'}",
        {"source_address_rejected": True},
    )


@pytest.mark.parametrize(
    ("case", "address", "fixed_key"),
    [
        ("legacy-omitted", "/exchange/output", None),
        ("legacy-empty", "/exchange/output/", None),
        ("legacy-nonempty", "/exchange/output/fixed.key", "fixed.key"),
        ("v2-omitted", "/exchanges/output", ""),
        ("v2-empty", "/exchanges/output/", ""),
        ("v2-nonempty", "/exchanges/output/fixed.key", "fixed.key"),
    ],
)
def test_amqp10_target_address_controls_actual_delivery(
    broker: _Broker, case: str, address: str, fixed_key: str | None
) -> None:
    broker.queue("input")
    broker.exchange("output")
    broker.queue("sink")
    expected_delivery_key = "delivery.subject" if fixed_key is None else fixed_key
    broker.bind("sink", expected_delivery_key)
    broker.start(
        {
            "src-protocol": "amqp10",
            "src-uri": broker.uri10,
            "src-address": "/queues/input",
            "dest-protocol": "amqp10",
            "dest-uri": broker.uri10,
            "dest-address": address,
        }
    )
    broker.running()
    broker.publish_native_subject()
    (message,) = broker.wait(lambda: broker.messages("sink"), "AMQP 1.0 delivery")
    assert message["payload"] == "delivery-proof"
    assert message["routing_key"] == expected_delivery_key
    broker.assert_endpoint("src", NodeKind.QUEUE, None)
    broker.assert_endpoint("dest", NodeKind.EXCHANGE, fixed_key)
    broker.record(
        f"amqp10-destination-{case}",
        {
            "amqp_address_v1_permitted": True,
            "message_subject": "delivery.subject",
            "payload_delivered": message["payload"],
            "delivered_key": message["routing_key"],
        },
    )
