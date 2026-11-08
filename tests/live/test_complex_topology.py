"""Import the saved demo into isolated vhosts, assert findings, then delete it.

No capture files are generated or updated by these tests. Import only the demo
resource sections: never overwrite the connected broker's users or cluster
parameters. Like the other live tests, run this on a disposable broker.
"""

from __future__ import annotations

import json
from contextlib import ExitStack, suppress
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, unquote, urlsplit, urlunsplit
from uuid import uuid4

import pytest
from tests.shared.complex_topology import CAPTURE, CHECKS, DEMO_PREFIX, ROLES
from tests.shared.live_broker import wait_for

from rabbitmq_management_sdk import ClusterAuditor, NotFoundError, RabbitMQClient

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from tests.conftest import RabbitSettings
    from tests.shared.live_broker import ManagementAPI

pytestmark = pytest.mark.live


def _rewrite_uri(uri: str, vhosts: dict[str, str], userinfo: str) -> str:
    parsed = urlsplit(uri)
    vhost = vhosts[unquote(parsed.path[1:])]
    return urlunsplit((parsed.scheme, f"{userinfo}@{parsed.netloc}", "/" + quote(vhost, safe=""), parsed.query, ""))


def _import_payload(prefix: str, username: str, password: str) -> dict[str, Any]:
    source = json.loads((CAPTURE / "full-export-definitions.json").read_bytes())
    vhosts = {f"{DEMO_PREFIX}.{role}": f"{prefix}.{role}" for role in ROLES}
    payload: dict[str, Any] = {
        "vhosts": [dict(vhost, name=vhosts[vhost["name"]]) for vhost in source["vhosts"] if vhost["name"] in vhosts],
        "permissions": [
            {"user": username, "vhost": vhost, "configure": ".*", "read": ".*", "write": ".*"}
            for vhost in vhosts.values()
        ],
    }
    for section in ("queues", "exchanges", "bindings", "parameters", "policies"):
        payload[section] = [
            dict(item, vhost=vhosts[item["vhost"]]) for item in source[section] if item["vhost"] in vhosts
        ]
    # Credentials are added only to this in-memory import, never to a fixture.
    userinfo = f"{quote(username, safe='')}:{quote(password, safe='')}"
    for parameter in payload["parameters"]:
        for side in ("src", "dest"):
            key = f"{side}-uri"
            value = parameter["value"][key]
            parameter["value"][key] = (
                [_rewrite_uri(uri, vhosts, userinfo) for uri in value]
                if isinstance(value, list)
                else _rewrite_uri(value, vhosts, userinfo)
            )
    return payload


@dataclass
class _DemoBroker:
    client: RabbitMQClient = field(repr=False)
    http: ManagementAPI = field(repr=False)
    prefix: str

    def api(self, method: str, path: str, body: dict[str, Any]) -> object:
        return self.http.request(method, path, body)

    def wait[T](self, callback: Callable[[], T], label: str) -> T:
        return wait_for(callback, f"demo {label}", timeout=40)

    def publish(
        self,
        role: str,
        exchange: str,
        key: str,
        payload: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> bool:
        return self.http.publish(f"{self.prefix}.{role}", exchange, key, payload, headers=headers)

    def messages(self, role: str, queue: str, *, ackmode: str = "ack_requeue_false") -> list[dict[str, Any]]:
        return self.http.messages(f"{self.prefix}.{role}", queue, ackmode=ackmode)

    def delivered(self, role: str, queue: str, payload: str) -> dict[str, Any]:
        return self.http.delivered(f"{self.prefix}.{role}", queue, payload)

    def auditor(self) -> ClusterAuditor:
        definitions = self.client.admin.export_definitions()
        vhosts = {f"{self.prefix}.{role}" for role in ROLES}
        definitions = definitions.model_copy(
            update={
                "vhosts": [vhost for vhost in definitions.vhosts if vhost.name in vhosts],
                **{
                    section: [item for item in getattr(definitions, section) if item.vhost in vhosts]
                    for section in ("queues", "exchanges", "bindings", "parameters", "policies")
                },
            }
        )
        return ClusterAuditor(
            definitions,
            queues=[queue for queue in self.client.queues.list_all(disable_stats=True) if queue.vhost in vhosts],
            exchanges=[
                exchange for exchange in self.client.exchanges.list_all(disable_stats=True) if exchange.vhost in vhosts
            ],
        )


def _delete_vhost(client: RabbitMQClient, vhost: str) -> None:
    with suppress(NotFoundError):
        client.admin.delete_vhost(vhost)


@pytest.fixture(scope="module")
def demo_broker(rabbit_config: RabbitSettings, management_api: ManagementAPI) -> Iterator[_DemoBroker]:
    prefix = f"sdk-demo-{uuid4().hex}"
    with ExitStack() as stack:
        client = stack.enter_context(RabbitMQClient(rabbit_config.client_config()))
        broker = _DemoBroker(client, management_api, prefix)
        # Register every cleanup before importing: partial setup failures are cleaned too.
        for role in ROLES:
            stack.callback(_delete_vhost, client, f"{prefix}.{role}")
        broker.api("POST", "definitions", _import_payload(prefix, rabbit_config.username, rabbit_config.password))
        broker.wait(
            lambda: (
                len(
                    [
                        status
                        for status in client.shovels.get_all_shovel_statuses()
                        if status.vhost.startswith(prefix + ".") and status.state in ("running", "flow")
                    ]
                )
                == 13
            ),
            "shovels to run",
        )
        yield broker


@pytest.fixture(scope="module")
def demo_auditor(demo_broker: _DemoBroker) -> ClusterAuditor:
    return demo_broker.auditor()


@pytest.mark.parametrize("check", CHECKS, ids=lambda check: check.__name__)
def test_imported_topology(
    demo_broker: _DemoBroker, demo_auditor: ClusterAuditor, check: Callable[[ClusterAuditor, str], None]
) -> None:
    check(demo_auditor, demo_broker.prefix)


def test_four_exchange_types_route_matching_messages(demo_broker: _DemoBroker) -> None:
    assert demo_broker.publish(
        "routing",
        "route.direct",
        "orders.eu.created",
        "four-types",
        headers={"format": "json", "region": "eu", "x-demo": "yes"},
    )
    demo_broker.delivered("routing", "route.classic", "four-types")
    demo_broker.delivered("routing", "route.quorum", "four-types")
    for mode in ("all", "any", "all-with-x", "any-with-x"):
        demo_broker.delivered("routing", "headers." + mode, "four-types")
    assert not demo_broker.publish("routing", "route.direct", "orders.us.ignored", "wrong-key")
    assert not demo_broker.publish(
        "routing", "route.direct", "orders.eu.created", "wrong-headers", headers={"format": "xml"}
    )


def test_alternate_chain_routes_unmatched_messages(demo_broker: _DemoBroker) -> None:
    assert demo_broker.publish("fallback", "fallback.primary", "unknown", "fallback-proof")
    demo_broker.delivered("fallback", "fallback.plq", "fallback-proof")
    assert demo_broker.messages("fallback", "fallback.accepted") == []
    assert demo_broker.publish("fallback", "fallback.primary", "known", "direct-proof")
    demo_broker.delivered("fallback", "fallback.accepted", "direct-proof")
    assert demo_broker.messages("fallback", "fallback.plq") == []


def test_topic_filters_and_fanout_ignores_the_routing_key(demo_broker: _DemoBroker) -> None:
    assert not demo_broker.publish(
        "routing", "route.topic", "billing.eu.created", "wrong-topic", headers={"format": "json"}
    )
    assert demo_broker.publish("routing", "route.fanout", "any.routing.key", "fanout-proof")
    demo_broker.delivered("routing", "route.classic", "fanout-proof")
    demo_broker.delivered("routing", "route.quorum", "fanout-proof")


def test_ttl_then_rejection_reaches_poison_parking_queue(demo_broker: _DemoBroker) -> None:
    assert demo_broker.publish("deadletter", "retry.in", "retry", "dead-letter-proof")
    message = demo_broker.wait(
        lambda: demo_broker.messages("deadletter", "work.quorum", ackmode="reject_requeue_false"),
        "TTL dead lettering",
    )[0]
    assert message["payload"] == "dead-letter-proof"
    parked = demo_broker.delivered("deadletter", "poison.plq", "dead-letter-proof")
    deaths = parked["properties"]["headers"]["x-death"]
    assert {(death["queue"], death["reason"]) for death in deaths} == {
        ("retry.wait", "expired"),
        ("work.quorum", "rejected"),
    }


def test_two_shovels_transfer_across_three_vhosts(demo_broker: _DemoBroker) -> None:
    assert demo_broker.publish("region-a", "pipeline.in", "demo.route", "pipeline-proof", headers={"format": "json"})
    demo_broker.delivered("region-c", "pipeline.q", "pipeline-proof")


def test_delivery_limit_parks_a_poison_message(demo_broker: _DemoBroker) -> None:
    assert demo_broker.publish("deadletter", "work.in", "work", "poison-proof")

    def reject_again() -> list[dict[str, Any]]:
        demo_broker.messages("deadletter", "work.quorum", ackmode="reject_requeue_true")
        return demo_broker.messages("deadletter", "poison.plq")

    # Each HTTP get rejects and requeues the message; the quorum queue's
    # configured delivery limit terminates this bounded verification.
    messages = demo_broker.wait(reject_again, "poison delivery limit")
    assert [message["payload"] for message in messages] == ["poison-proof"]
    assert messages[0]["properties"]["headers"]["x-death"][0]["reason"] == "delivery_limit"


def test_selected_policies_and_argument_overrides_route_deliveries(demo_broker: _DemoBroker) -> None:
    for queue, destination in (("policy.work", "policy.dlx.q"), ("policy.override", "argument.dlx.q")):
        assert demo_broker.publish("policies", "policy.in", queue, queue)
        demo_broker.wait(
            partial(demo_broker.messages, "policies", queue, ackmode="reject_requeue_false"),
            "policy queue rejection",
        )
        demo_broker.delivered("policies", destination, queue)
    for exchange, destination in (
        ("policy.primary", "policy.fallback.q"),
        ("policy.override.exchange", "argument.fallback.q"),
    ):
        assert demo_broker.publish("policies", exchange, "unmatched", exchange)
        demo_broker.delivered("policies", destination, exchange)


def test_competing_shovels_share_work_instead_of_broadcasting(demo_broker: _DemoBroker) -> None:
    payloads = {f"work-{index}" for index in range(6)}
    for payload in sorted(payloads):
        assert demo_broker.publish("region-a", "compete.in", "", payload)
    received: list[str] = []

    def collect() -> bool:
        for role in ("region-b", "region-c"):
            received.extend(message["payload"] for message in demo_broker.messages(role, "compete.sink"))
        return len(received) >= len(payloads)

    demo_broker.wait(collect, "competing deliveries")
    assert len(received) == len(payloads)
    assert set(received) == payloads
