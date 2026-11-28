"""Exercise cluster capture through the public HTTP and file boundaries."""

from __future__ import annotations

import json
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import httpx2
import pytest
from pydantic import SecretStr
from tests.shared.parser_fixtures import _binding, _exchange, _observed_queue, _queue, _vhost

from rabbitmq_management_sdk import (
    AuditCapture,
    AuditCaptureError,
    ClusterAuditor,
    Config,
    RabbitMQClient,
    RabbitMQVersion,
    compare_captures,
)
from rabbitmq_management_sdk.http_adapter import factory
from rabbitmq_management_sdk.http_adapter.httpx2 import Httpx2Adapter
from rabbitmq_management_sdk.topology import (
    BindingComparison,
    ComparisonEvidence,
    ComparisonGap,
    EdgeKind,
    NodeId,
    NodeKind,
    RoutingCondition,
    RoutingContract,
    RoutingVerdict,
    TopologyEdge,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from ssl import SSLContext

pytestmark = pytest.mark.integration
_FIXTURES = Path(__file__).parent / "rmq-4.2"


def _client(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx2.Request], httpx2.Response],
    *,
    detect_version: bool = False,
) -> RabbitMQClient:
    def adapter(
        host: str, *, port: int, default_headers: dict[str, str], ssl_context: SSLContext | None
    ) -> Httpx2Adapter:
        return Httpx2Adapter(
            host,
            port=port,
            default_headers=default_headers,
            ssl_context=ssl_context,
            transport=httpx2.MockTransport(handler),
        )

    monkeypatch.setattr(factory, "create_adapter", adapter)
    return RabbitMQClient(
        Config(
            host="management.internal",
            username="audit-login",
            password=SecretStr("client-credential-value"),
            virtual_host="configured/vhost",
            version_override=None if detect_version else RabbitMQVersion.parse("4.3.8"),
        )
    )


def _page(request: httpx2.Request, rows: list[dict[str, Any]]) -> dict[str, Any]:
    number = int(request.url.params["page"])
    size = int(request.url.params["page_size"])
    offset = (number - 1) * size
    items = rows[offset : offset + size]
    return {
        "page": number,
        "page_size": size,
        "page_count": (len(rows) + size - 1) // size,
        "item_count": len(items),
        "filtered_count": len(rows),
        "total_count": len(rows),
        "items": items,
    }


def test_collect_multiple_pages_and_replay_existing_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    definitions_path = _FIXTURES / "full-export-definitions.json"
    queues_path = _FIXTURES / "queues-get-all-no-stats.json"
    exchanges_path = _FIXTURES / "exchanges-get-all-no-stats.json"
    definitions = json.loads(definitions_path.read_text())
    listings = {
        "/api/queues": json.loads(queues_path.read_text()),
        "/api/exchanges": json.loads(exchanges_path.read_text()),
    }
    requested: list[tuple[str, dict[str, str]]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "GET"
        path = request.url.path
        requested.append((path, dict(request.url.params)))
        if path == "/api/definitions":
            return httpx2.Response(200, json=definitions)
        return httpx2.Response(200, json=_page(request, listings[path]))

    with _client(monkeypatch, handler) as client:
        capture = client.collect_audit_capture(page_size=10)

    expected: list[tuple[str, dict[str, str]]] = [("/api/definitions", {})]
    for endpoint, page_count in (("/api/queues", 2), ("/api/exchanges", 8)):
        expected.extend((endpoint, {"page": str(page), "page_size": "10"}) for page in range(1, page_count + 1))
    assert requested == expected
    manifest = capture.manifest
    assert manifest.schema_version == 1
    assert manifest.scope == "cluster"
    assert manifest.cluster_name == "my-lab-rabbit"
    assert manifest.cluster_id == "rabbitmq-cluster-id-8ybq49F1lPFVBGa2QKGeCQ"
    assert manifest.broker_version == definitions["rabbitmq_version"]
    assert manifest.broker_version_source == "definitions"
    assert manifest.queues.pages[-1].item_count == 3
    assert manifest.exchanges.pages[-1].page == 8
    assert manifest.exchanges.pages[-1].total_count == 76
    times = [
        time
        for endpoint in (manifest.definitions, manifest.queues, manifest.exchanges)
        for time in (endpoint.started_at, endpoint.finished_at)
    ]
    assert times == sorted(times)
    assert all(time.tzinfo is not None for time in times)

    path = tmp_path / "capture.json"
    capture.save(path)
    loaded = AuditCapture.load(path)
    assert loaded.manifest == manifest
    hosts = {"localhost"}
    reference = ClusterAuditor.from_files(
        definitions_path,
        queues_path=queues_path,
        exchanges_path=exchanges_path,
        in_cluster_amqp_hosts=hosts,
    )
    replay = loaded.to_auditor(in_cluster_amqp_hosts=hosts)
    assert replay.topology == reference.topology
    assert replay.audit() == capture.to_auditor(in_cluster_amqp_hosts=hosts).audit() == reference.audit()
    assert replay.message_loop_candidates().cycles
    assert replay.quorum_delivery_configuration()
    comparison = compare_captures(capture, loaded)
    assert comparison.cluster_id == manifest.cluster_id
    assert comparison.bindings.added == comparison.bindings.removed == ()
    assert comparison.queues.added == comparison.queues.removed == ()
    assert comparison.policy_selections.changed == ()


@pytest.mark.parametrize("later", ["failed-page", "omitted-bindings", "empty-bindings"])
def test_compare_saved_captures_after_binding_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, later: str
) -> None:
    cluster_id = "rabbitmq-cluster-id-comparison"
    vhost = "orders"
    origin = NodeId(vhost, "events", NodeKind.EXCHANGE, cluster_id)
    worker = NodeId(vhost, "worker", NodeKind.QUEUE, cluster_id)
    contract = RoutingContract("Empty key reaches worker", origin, "", (RoutingCondition.reaches_all({worker}),))
    exchanges = [_exchange(origin.name, vhost), _exchange("spare", vhost)]
    definitions = {
        "global_parameters": [{"name": "internal_cluster_id", "value": cluster_id}],
        "vhosts": [_vhost(vhost)],
        "queues": [_queue(worker.name, vhost)],
        "exchanges": exchanges,
        "policies": [],
        "parameters": [],
        "bindings": [_binding(origin.name, worker.name, vhost=vhost)],
    }
    observations = {
        "/api/queues": [
            _observed_queue(
                worker.name,
                vhost,
                type="classic",
                arguments={"x-queue-type": "classic"},
                policy=None,
                operator_policy=None,
            ).model_dump(mode="json", by_alias=True, exclude_unset=True)
        ],
        "/api/exchanges": [dict(exchange, policy=None, operator_policy=None) for exchange in exchanges],
    }
    fail_second_page = False

    def handler(request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if path == "/api/definitions":
            return httpx2.Response(200, json=definitions)
        if fail_second_page and path == "/api/exchanges" and request.url.params["page"] == "2":
            return httpx2.Response(403)
        return httpx2.Response(200, json=_page(request, observations[path]))

    before_path, after_path = tmp_path / "before.json", tmp_path / "after.json"
    with _client(monkeypatch, handler) as client:
        original = client.collect_audit_capture(page_size=1)
        original.save(before_path)
        baseline_bytes = before_path.read_bytes()
        if later == "failed-page":
            fail_second_page = True
            with pytest.raises(AuditCaptureError, match=r"/api/exchanges page 2 \(HTTP 403\)"):
                client.collect_audit_capture(page_size=1).save(after_path)
        else:
            if later == "omitted-bindings":
                del definitions["bindings"]
            else:
                definitions["bindings"] = []
            client.collect_audit_capture(page_size=1).save(after_path)

    baseline = AuditCapture.load(before_path)
    assert before_path.read_bytes() == baseline_bytes
    if later == "failed-page":
        assert not after_path.exists()
        replay = compare_captures(baseline, original, routing_contracts=[contract])
        assert replay.bindings == BindingComparison()
        (routing,) = replay.routing_contracts
        assert routing.before == routing.after
        assert routing.before.verdict is RoutingVerdict.SATISFIED
        return

    comparison = compare_captures(baseline, AuditCapture.load(after_path), routing_contracts=[contract])
    for category in (comparison.vhosts, comparison.queues, comparison.exchanges):
        assert not (category.indeterminate or category.added or category.removed or category.attributes)
    assert not (comparison.policy_selections.indeterminate or comparison.policy_selections.changed)

    gaps = (ComparisonGap.BINDINGS_NOT_CAPTURED,) if later == "omitted-bindings" else ()
    removed = () if gaps else (TopologyEdge(origin, worker, EdgeKind.BINDING, ""),)
    assert comparison.bindings == BindingComparison(removed=removed, evidence=ComparisonEvidence(after=gaps))
    assert comparison.bindings.indeterminate == bool(gaps)

    (routing,) = comparison.routing_contracts
    after_verdict = RoutingVerdict.UNDETERMINED if gaps else RoutingVerdict.VIOLATED
    assert (routing.before.verdict, routing.after.verdict) == (RoutingVerdict.SATISFIED, after_verdict)
    assert (routing.before.gaps, routing.after.gaps) == ((), gaps)


@pytest.mark.parametrize("reject", ["status", "schema", "changed_counts", "duplicate"])
def test_reject_later_page_without_publishing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reject: str) -> None:
    requested: list[tuple[str, str | None]] = []
    rows = [
        {"name": name, "vhost": "/", "type": "direct", "durable": True, "auto_delete": False, "internal": False}
        for name in ("first", "second", "third")
    ]

    def handler(request: httpx2.Request) -> httpx2.Response:
        path, number = request.url.path, request.url.params.get("page")
        requested.append((path, number))
        if path == "/api/definitions":
            return httpx2.Response(200, json={"vhosts": []})
        if path == "/api/queues":
            return httpx2.Response(200, json=_page(request, []))
        envelope = _page(request, rows)
        if number == "2":
            if reject == "status":
                return httpx2.Response(403, json={"reason": "broker-credential-in-error"})
            if reject == "schema":
                envelope["items"] = [{"secret": "broker-credential-in-error"}]
            if reject == "changed_counts":
                envelope["total_count"] += 1
            if reject == "duplicate":
                envelope["items"] = rows[:1]
        return httpx2.Response(200, json=envelope)

    with _client(monkeypatch, handler) as client, pytest.raises(AuditCaptureError) as caught:
        client.collect_audit_capture(page_size=1).save(tmp_path / "capture.json")

    assert requested[:4] == [
        ("/api/definitions", None),
        ("/api/queues", "1"),
        ("/api/exchanges", "1"),
        ("/api/exchanges", "2"),
    ]
    if reject != "duplicate":
        assert len(requested) == 4
    assert "broker-credential-in-error" not in "".join(traceback.format_exception(caught.value))
    assert caught.value.__context__ is None
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("vhost_suffix", "hostname_suffix", "second_header"),
    [
        pytest.param("", "", "second-header-secret", id="unicode"),
        pytest.param("&#65;", "%26%2338%3B%2365%3B", "second-header-secret", id="literal-decimal-reference"),
        pytest.param("", "", "[redacted 1]", id="existing-redaction"),
    ],
)
def test_sanitization_keeps_presence_and_raw_join_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, vhost_suffix: str, hostname_suffix: str, second_header: str
) -> None:
    vhost = f"v/h%🦊{vhost_suffix}"
    queue = "q/%2f🔥"
    exchange = "amq://route-segment"
    hostname = quote("vhost:v/h%🦊".encode().decode("latin-1"), safe=":") + hostname_suffix
    definitions: dict[str, Any] = {
        "vhosts": [{"name": vhost, "metadata": {"default_queue_type": "quorum"}, "description": "vhost-secret"}],
        "queues": [{"name": queue, "vhost": vhost, "durable": True, "auto_delete": False}],
        "exchanges": [
            {"name": exchange, "vhost": vhost, "type": "headers", "durable": True, "auto_delete": False},
        ],
        "bindings": [
            {
                "vhost": vhost,
                "source": exchange,
                "destination": queue,
                "destination_type": "queue",
                "routing_key": "",
                "arguments": {"x-match": "all", "authorization": token},
            }
            for token in ("first-header-secret", second_header)
        ],
        "rabbitmq_version": "4.3.8",
        "users": [
            {"name": "account", "password_hash": "password-hash-secret", "hashing_algorithm": "sha256", "tags": []}
        ],
        "permissions": [],
        "policies": [],
        "parameters": [
            {
                "component": "shovel",
                "name": "forward",
                "vhost": vhost,
                "value": {
                    "src-uri": [
                        f"amqp://user:uri-userinfo-secret@local/{quote(vhost, safe='')}?token=query-secret",
                        "amqp://malformed-secret@@",
                    ],
                    "src-queue": queue,
                    "dest-protocol": "amqp10",
                    "dest-uri": f"amqps://user:uri-userinfo-secret@local?hostname={hostname}&key=query-secret",
                    "dest-address": f"/queues/{quote(queue, safe='')}",
                    "ack-mode": None,
                    "password": "shovel-option-secret",
                },
            },
            {"component": "vhost-limits", "name": "limits", "vhost": vhost, "value": []},
            {
                "component": "federation-upstream",
                "name": "upstream",
                "vhost": vhost,
                "value": {"uri": "amqp://user:federation-secret@upstream"},
            },
        ],
    }
    queues = [
        {
            "name": queue,
            "vhost": vhost,
            "type": "quorum",
            "arguments": {"x-queue-type": "quorum", "x-delayed-retry-min": None, "x-dead-letter-exchange": exchange},
            "policy": None,
            "effective_policy_definition": {},
            "durable": True,
            "auto_delete": False,
            "node": "rabbit@local",
            "state": "running",
            "members": ["rabbit@local"],
            "leader": None,
            "online": [],
        }
    ]
    exchanges = [{**definitions["exchanges"][0], "arguments": {}, "internal": False}]

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/api/definitions":
            return httpx2.Response(200, json=definitions)
        return httpx2.Response(200, json=_page(request, queues if request.url.path == "/api/queues" else exchanges))

    path = tmp_path / "capture.json"
    with _client(monkeypatch, handler) as client:
        client.collect_audit_capture().save(path)

    saved = path.read_text()
    assert "-secret" not in saved
    assert "management.internal" not in saved
    assert "client-credential-value" not in saved
    data = json.loads(saved)
    assert data["definitions"]["permissions"] == data["definitions"]["policies"] == []
    assert "topic_permissions" not in data["definitions"]
    assert "arguments" not in data["definitions"]["queues"][0]
    assert "arguments" not in data["definitions"]["exchanges"][0]
    assert data["definitions"]["parameters"][-1]["value"] == []
    assert data["queues"][0]["arguments"]["x-delayed-retry-min"] is None
    assert data["queues"][0]["policy"] is None
    assert "operator_policy" not in data["queues"][0]
    assert data["queues"][0]["effective_policy_definition"] == {}
    assert data["queues"][0]["online"] == []
    assert data["queues"][0]["leader"] is None
    assert data["exchanges"][0]["arguments"] == {}

    loaded = AuditCapture.load(path)
    replay = loaded.to_auditor(in_cluster_amqp_hosts={"local"})
    original_paths = [tmp_path / f"original-{kind}.json" for kind in ("definitions", "queues", "exchanges")]
    for source_path, source_data in zip(original_paths, (definitions, queues, exchanges), strict=True):
        source_path.write_text(json.dumps(source_data))
    original = ClusterAuditor.from_files(
        original_paths[0],
        queues_path=original_paths[1],
        exchanges_path=original_paths[2],
        in_cluster_amqp_hosts={"local"},
    )
    assert {node.id for node in replay.topology.nodes} == {node.id for node in original.topology.nodes}
    assert next(iter(replay.topology.shovels)).destination.vhost == vhost
    assert replay.shovel_safety() == original.shovel_safety()
    assert replay.quorum_delivery_configuration() == original.quorum_delivery_configuration()
    assert sum(edge.kind == EdgeKind.BINDING for edge in replay.topology.edges) == 2
    assert any(edge.kind == EdgeKind.DEAD_LETTER and edge.target.name == exchange for edge in replay.topology.edges)
    copy = tmp_path / "copy.json"
    loaded.save(copy)
    assert copy.read_bytes() == path.read_bytes()


def test_malformed_shovel_resource_fields_are_sanitized_on_collect_and_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = "synthetic-shovel-secret"
    malformed_fields = {
        "src-queue": {"password": marker},
        "src-exchange-key": [marker],
        "dest-address": {"url": marker},
    }
    value = {"src-uri": "amqp://", "dest-uri": "amqp://", "dest-queue": "amqp://literal-queue", **malformed_fields}
    definitions = {
        "vhosts": [_vhost("/")],
        "parameters": [{"component": "shovel", "vhost": "/", "name": "forward", "value": value}],
    }

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/api/definitions":
            return httpx2.Response(200, json=definitions)
        return httpx2.Response(200, json=_page(request, []))

    collected = tmp_path / "collected.json"
    with _client(monkeypatch, handler) as client:
        client.collect_audit_capture().save(collected)

    sanitized = collected.read_text()
    assert marker not in sanitized
    bundle = json.loads(sanitized)
    retained = bundle["definitions"]["parameters"][0]["value"]
    assert all(retained[key] is None for key in malformed_fields)
    assert retained["dest-queue"] == value["dest-queue"]

    # Loading an edited bundle also reapplies sanitization.
    retained.update(malformed_fields)
    previous = tmp_path / "previous.json"
    previous.write_text(json.dumps(bundle))
    replay = AuditCapture.load(previous)
    (shovel,) = replay.to_auditor().topology.shovels
    assert shovel.source.resource_name is None
    assert shovel.destination.resource_name == value["dest-queue"]
    re_saved = tmp_path / "re-saved.json"
    replay.save(re_saved)
    assert marker not in re_saved.read_text()


def test_compare_saved_shovel_settings_with_redacted_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    value = {
        "src-uri": "amqp://",
        "src-queue": "source",
        "dest-uri": "amqp://",
        "dest-queue": "target",
        "src-prefetch-count": 1000,
        "src-predeclared": False,
        "src-consumer-args": {"x-stream-offset": "first", "token": "synthetic-option-secret"},
    }
    definitions = {
        "global_parameters": [{"name": "internal_cluster_id", "value": "rabbitmq-cluster-id-shovel"}],
        "vhosts": [_vhost("/")],
        "parameters": [{"component": "shovel", "vhost": "/", "name": "forward", "value": value}],
    }

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/api/definitions":
            return httpx2.Response(200, json=definitions)
        return httpx2.Response(200, json=_page(request, []))

    before_path, after_path = tmp_path / "before.json", tmp_path / "after.json"
    with _client(monkeypatch, handler) as client:
        client.collect_audit_capture().save(before_path)
        value["src-prefetch-count"] = 50
        value["dest-queue"] = "new-target"
        client.collect_audit_capture().save(after_path)

    assert "synthetic-option-secret" not in before_path.read_text()
    before_json = json.loads(before_path.read_text())
    retained = before_json["definitions"]["parameters"][0]["value"]
    assert retained["src-prefetch-count"] == 1000
    assert retained["src-predeclared"] is False
    assert retained["src-consumer-args"]["x-stream-offset"] == "first"

    after = AuditCapture.load(after_path)
    comparison = compare_captures(AuditCapture.load(before_path), after).shovels
    (change,) = comparison.attributes
    assert change.changed_fields == ("value", "src-prefetch-count")
    assert change.indeterminate_fields == ("src-consumer-args",)


@pytest.mark.parametrize("detect_version", [True, False])
def test_empty_resources_and_version_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, detect_version: bool
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/api/overview":
            return httpx2.Response(200, json={"rabbitmq_version": "4.3.8", "cluster_name": "broker"})
        if request.url.path == "/api/definitions":
            return httpx2.Response(200, json={"vhosts": [], "rabbitmq_version": None})
        return httpx2.Response(200, json=_page(request, []))

    path = tmp_path / "capture.json"
    with _client(monkeypatch, handler, detect_version=detect_version) as client:
        client.collect_audit_capture().save(path)
    capture = AuditCapture.load(path)
    assert capture.manifest.broker_version == ("4.3.8" if detect_version else None)
    assert capture.manifest.broker_version_source == ("overview" if detect_version else None)
    assert capture.manifest.cluster_name == ("broker" if detect_version else None)
    (page,) = capture.manifest.queues.pages
    assert (page.page, page.page_count, page.item_count) == (1, 0, 0)
    assert not capture.to_auditor().topology.nodes
    data = json.loads(path.read_text())
    assert data["definitions"]["rabbitmq_version"] is None
    assert data["queues"] == data["exchanges"] == []


def test_interrupted_save_and_refusal_to_overwrite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        payload = {"vhosts": []} if request.url.path == "/api/definitions" else _page(request, [])
        return httpx2.Response(200, json=payload)

    with _client(monkeypatch, handler) as client:
        capture = client.collect_audit_capture()

    def interrupt(_fd: int) -> None:
        assert not path.exists()
        raise KeyboardInterrupt

    path = tmp_path / "capture.json"
    with monkeypatch.context() as patch:
        patch.setattr("rabbitmq_management_sdk.client.audit_capture.os.fsync", interrupt)
        with pytest.raises(KeyboardInterrupt):
            capture.save(path)
    assert list(tmp_path.iterdir()) == []
    capture.save(path)
    original = path.read_bytes()
    with pytest.raises(AuditCaptureError, match="already exists"):
        capture.save(path)
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]

    data = json.loads(original)
    data["manifest"]["schema_version"] = 2
    path.write_text(json.dumps(data))
    with pytest.raises(AuditCaptureError, match="expected a version 1 bundle"):
        AuditCapture.load(path)

    data = json.loads(original)
    data.pop("exchanges")
    path.write_text(json.dumps(data))
    with pytest.raises(AuditCaptureError, match="version 1"):
        AuditCapture.load(path)
    path.write_text(original.decode().removesuffix("}"))
    with pytest.raises(AuditCaptureError, match="version 1"):
        AuditCapture.load(path)
