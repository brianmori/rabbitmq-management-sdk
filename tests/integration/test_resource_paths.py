"""Resource names and binding keys remain raw until the URL boundary."""

from __future__ import annotations

import json

import httpx2
import pytest

from rabbitmq_management_sdk import (
    BindingRequest,
    ClassicQueueRequest,
    ExchangeRequest,
    QueueRequest,
    VhostLimitName,
    VhostLimitRequest,
    VhostRequest,
)
from rabbitmq_management_sdk.http_adapter.httpx2 import Httpx2Adapter
from rabbitmq_management_sdk.resources.v4.admin.services import AdminManager
from rabbitmq_management_sdk.resources.v4.bindings.services import BindingManager
from rabbitmq_management_sdk.resources.v4.exchanges.services import ExchangeManager
from rabbitmq_management_sdk.resources.v4.policies.services import OperatorPolicyManager, PolicyManager
from rabbitmq_management_sdk.resources.v4.queues.services import QueueManager
from rabbitmq_management_sdk.resources.v4.shovels.services import ShovelManager

pytestmark = pytest.mark.integration

_NAMES = [
    ("orders/eu?#% café", "orders%2Feu%3F%23%25%20caf%C3%A9"),
    ("already%2F", "already%252F"),
    (".", "%2E"),
    ("..", "%2E%2E"),
]


@pytest.mark.parametrize("name,encoded", _NAMES)
def test_queue_crud_encodes_each_raw_name_once(name: str, encoded: str) -> None:
    seen: list[tuple[str, bytes]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append((request.method, request.url.raw_path))
        if request.method == "GET":
            return httpx2.Response(
                200,
                json={
                    "name": name,
                    "vhost": "/",
                    "durable": True,
                    "auto_delete": False,
                    "exclusive": False,
                    "type": "classic",
                    "node": "rabbit@node",
                    "state": "running",
                    "arguments": {"x-queue-type": "classic"},
                },
            )
        return httpx2.Response(204)

    with Httpx2Adapter("localhost", port=15672, transport=httpx2.MockTransport(handler)) as adapter:
        manager = QueueManager(adapter, "%2F", False)
        manager.create(name, QueueRequest(arguments=ClassicQueueRequest()))
        assert manager.get(name).name == name
        manager.delete(name)

    path = f"/api/queues/%2F/{encoded}".encode()
    assert seen == [("PUT", path), ("GET", path), ("DELETE", path)]


@pytest.mark.parametrize("name,encoded", _NAMES)
def test_exchange_crud_encodes_each_raw_name_once(name: str, encoded: str) -> None:
    seen: list[tuple[str, bytes]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append((request.method, request.url.raw_path))
        if request.method == "GET":
            return httpx2.Response(
                200,
                json={
                    "name": name,
                    "vhost": "/",
                    "type": "direct",
                    "durable": True,
                    "auto_delete": False,
                    "internal": False,
                    "arguments": {},
                },
            )
        return httpx2.Response(204)

    with Httpx2Adapter("localhost", port=15672, transport=httpx2.MockTransport(handler)) as adapter:
        manager = ExchangeManager(adapter, "%2F", False)
        manager.create(name, ExchangeRequest())
        assert manager.get(name).name == name
        manager.delete(name)

    path = f"/api/exchanges/%2F/{encoded}".encode()
    assert seen == [("PUT", path), ("GET", path), ("DELETE", path)]


@pytest.mark.parametrize("name,encoded", _NAMES)
@pytest.mark.parametrize(
    "manager_type,resource",
    [(PolicyManager, "policies"), (OperatorPolicyManager, "operator-policies"), (ShovelManager, "parameters/shovel")],
)
def test_policy_and_shovel_deletion_encode_resource_names(
    name: str,
    encoded: str,
    manager_type: type[PolicyManager] | type[OperatorPolicyManager] | type[ShovelManager],
    resource: str,
) -> None:
    seen: list[bytes] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.url.raw_path)
        return httpx2.Response(204)

    with Httpx2Adapter("localhost", port=15672, transport=httpx2.MockTransport(handler)) as adapter:
        manager_type(adapter, "%2F", False).delete(name)

    assert seen == [f"/api/{resource}/%2F/{encoded}".encode()]


@pytest.mark.parametrize("vhost,encoded", [("/", "%2F"), *_NAMES])
def test_admin_methods_encode_raw_vhosts(vhost: str, encoded: str) -> None:
    seen: list[tuple[str, bytes]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append((request.method, request.url.raw_path))
        if request.method != "GET":
            return httpx2.Response(204)
        if request.url.raw_path.startswith(b"/api/definitions/"):
            return httpx2.Response(
                200,
                json={
                    "rabbit_version": "4.3.4",
                    "rabbitmq_version": "4.3.4",
                    "product_name": "RabbitMQ",
                    "product_version": "4.3.4",
                    "rabbitmq_definition_format": "single_virtual_host",
                    "original_vhost_name": vhost,
                    "explanation": "",
                    "metadata": {},
                    "limits": [],
                },
            )
        if request.url.raw_path.startswith(b"/api/vhost-limits/"):
            return httpx2.Response(200, json=[{"vhost": vhost, "value": {"max-connections": 5}}])
        return httpx2.Response(200, json={"name": vhost, "tracing": False})

    with Httpx2Adapter("localhost", port=15672, transport=httpx2.MockTransport(handler)) as adapter:
        manager = AdminManager(adapter, False)
        manager.create_vhost(vhost, VhostRequest())
        assert manager.get_vhost(vhost).name == vhost
        manager.enable_vhost_deletion_protection(vhost)
        manager.disable_vhost_deletion_protection(vhost)
        limits = manager.get_vhost_limits(vhost)
        assert limits is not None and limits.vhost == vhost
        manager.apply_vhost_limit(vhost, VhostLimitName.MAX_CONNECTIONS, VhostLimitRequest(value=5))
        manager.delete_vhost_limit(vhost, VhostLimitName.MAX_CONNECTIONS)
        assert manager.export_vhost_definitions(vhost).original_vhost_name == vhost
        manager.delete_vhost(vhost)

    vhost_path = f"/api/vhosts/{encoded}"
    limits_path = f"/api/vhost-limits/{encoded}"
    expected = [
        ("PUT", vhost_path),
        ("GET", vhost_path),
        ("POST", f"{vhost_path}/deletion/protection"),
        ("DELETE", f"{vhost_path}/deletion/protection"),
        ("GET", limits_path),
        ("PUT", f"{limits_path}/max-connections"),
        ("DELETE", f"{limits_path}/max-connections"),
        ("GET", f"/api/definitions/{encoded}"),
        ("DELETE", vhost_path),
    ]
    assert seen == [(method, path.encode()) for method, path in expected]


@pytest.mark.parametrize("destination_type", ["queue", "exchange"])
@pytest.mark.parametrize(
    "key,encoded", [("routing/key?#%", "routing%2Fkey%3F%23%25"), ("%2F", "%252F"), (".", "%2E"), ("..", "%2E%2E")]
)
def test_binding_keys_and_vhost_match_broker_reads_and_encode_once(
    destination_type: str, key: str, encoded: str
) -> None:
    seen: list[tuple[str, bytes]] = []
    suffix = "q" if destination_type == "queue" else "e"
    base_path = f"/api/bindings/v%2Fa%25/e/source%2Fname/{suffix}/target%3F%23%25"
    wire_binding = {
        "source": "source/name",
        "destination": "target?#%",
        "destination_type": destination_type,
        "routing_key": key,
        "arguments": {},
        "vhost": "v/a%",
        "properties_key": key,
    }

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append((request.method, request.url.raw_path))
        if request.method == "POST":
            assert json.loads(request.content)["routing_key"] == key
            return httpx2.Response(
                201, headers={"Location": f"http://localhost{base_path}/{encoded}?ignored=1#fragment"}
            )
        if request.method == "GET":
            return httpx2.Response(200, json=[wire_binding])
        return httpx2.Response(204)

    with Httpx2Adapter("localhost", port=15672, transport=httpx2.MockTransport(handler)) as adapter:
        manager = BindingManager(adapter, "v%2Fa%25", False)
        if destination_type == "queue":
            created = manager.create_exchange_to_queue("source/name", "target?#%", BindingRequest(routing_key=key))
            observed = manager.list_exchange_to_queue("source/name", "target?#%")[0]
            manager.delete_exchange_to_queue(created.source, created.destination, created.properties_key)
        else:
            created = manager.create_exchange_to_exchange("source/name", "target?#%", BindingRequest(routing_key=key))
            observed = manager.list_exchange_to_exchange("source/name", "target?#%")[0]
            manager.delete_exchange_to_exchange(created.source, created.destination, created.properties_key)

    assert created == observed
    assert created.vhost == "v/a%" and created.properties_key == key
    assert seen == [
        ("POST", base_path.encode()),
        ("GET", base_path.encode()),
        ("DELETE", f"{base_path}/{encoded}".encode()),
    ]
