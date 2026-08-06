"""Integration tests for paginated queue and exchange listing."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import httpx
import pytest

from rabbitmq_management_sdk import ClusterAuditor
from rabbitmq_management_sdk.client.policy_selection import build_user_policy_selections
from rabbitmq_management_sdk.exceptions import TopologyValidationError
from rabbitmq_management_sdk.http_adapter.httpx import HttpxAdapter
from rabbitmq_management_sdk.resources.v4.admin.schemas.export_response import ClusterDefinitionsResponse
from rabbitmq_management_sdk.resources.v4.exchanges.schemas.exchange_response import ExchangeResponse
from rabbitmq_management_sdk.resources.v4.exchanges.services import ExchangeManager
from rabbitmq_management_sdk.resources.v4.queues.schemas.queue_response import QueueResponse
from rabbitmq_management_sdk.resources.v4.queues.services import QueueManager
from rabbitmq_management_sdk.topology.models import EdgeKind, NodeId, NodeKind

_FIXTURE_DIRECTORY = Path(__file__).parent / "rmq-4.2"


def _assert_captured_full_export_audit(auditor: ClusterAuditor) -> None:
    """Assert the stable topology facts captured from the RabbitMQ 4.2 lab."""
    report = auditor.audit()
    topology = auditor._topology

    assert auditor.cluster_id == "rabbitmq-cluster-id-8ybq49F1lPFVBGa2QKGeCQ"
    assert auditor.cluster_name == "my-lab-rabbit"
    assert auditor.cluster_label == "my-lab-rabbit"
    assert len(auditor.definitions.exchanges) == 19
    assert len(auditor.definitions.queues) == 13

    assert len(topology.exchanges) == 19
    assert len(topology.queues) == 13
    assert len(topology.shovels) == 4
    assert len(topology.edges) == 18
    assert Counter(edge.kind for edge in topology.edges) == {
        EdgeKind.BINDING: 5,
        EdgeKind.DEAD_LETTER: 3,
        EdgeKind.ALTERNATE_EXCHANGE: 2,
        EdgeKind.SHOVEL: 8,
    }

    (cycle,) = report.structural_cycles.cycles
    assert report.structural_cycles.truncated is False
    assert report.message_loop_candidates.cycles == (cycle,)
    assert report.message_loop_candidates.truncated is False
    assert [(node.kind, node.vhost, node.name) for node in cycle.nodes] == [
        (NodeKind.QUEUE, "src", "fan.q"),
        (NodeKind.EXCHANGE, "src", "my-fan.dlx"),
    ]
    assert [edge.kind for edge in cycle.edges] == [EdgeKind.DEAD_LETTER, EdgeKind.BINDING]

    orders_dlx = next(
        edge
        for edge in topology.edges
        if edge.kind == EdgeKind.DEAD_LETTER and edge.source.vhost == "policy" and edge.source.name == "orders.q"
    )
    events_ae = next(
        edge
        for edge in topology.edges
        if edge.kind == EdgeKind.ALTERNATE_EXCHANGE and edge.source.vhost == "policy" and edge.source.name == "events"
    )
    assert orders_dlx.target.name == "dlx.b"
    assert events_ae.target.name == "ae.b"

    assert len(report.black_hole_exchanges) == 13
    assert [exchange.id.name for exchange in report.unreachable_internal_exchanges] == ["int-transient-auto-del"]
    assert len(report.queues_without_declared_ingress) == 8
    assert [shovel.id.name for shovel in report.cross_vhost_shovels] == [
        "test-shovel",
        "test-shovel-local",
        "test-shovel-local-multiple",
        "test-shovel-10",
    ]
    assert report.shovels_with_unconfirmed_endpoints == ()
    assert report.shovels_with_unresolved_vhost == ()
    assert report.dangling_edges == ()


def _queue(name: str) -> dict[str, object]:
    return {
        "arguments": {"x-queue-type": "classic"},
        "auto_delete": False,
        "durable": True,
        "exclusive": False,
        "name": name,
        "node": "rabbit@node",
        "state": "running",
        "type": "classic",
        "vhost": "/",
    }


def _exchange(name: str) -> dict[str, object]:
    return {
        "arguments": {},
        "auto_delete": False,
        "durable": True,
        "internal": False,
        "name": name,
        "type": "direct",
        "vhost": "/",
    }


def _page(items: list[dict[str, object]], *, page: int, page_count: int, page_size: int) -> dict[str, object]:
    return {
        "filtered_count": 2,
        "item_count": len(items),
        "items": items,
        "page": page,
        "page_count": page_count,
        "page_size": page_size,
        "total_count": 2,
    }


@pytest.mark.integration
def test_queue_list_page_sends_pagination_filter_and_disable_stats() -> None:
    seen: list[httpx.QueryParams] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params)
        return httpx.Response(200, json=_page([_queue("orders.q")], page=2, page_count=2, page_size=50))

    manager = QueueManager(
        http_client=HttpxAdapter(host="localhost", port=15672, transport=httpx.MockTransport(handler)),
        vhost="%2F",
        strict=False,
    )

    result = manager.list_page(
        page=2,
        page_size=50,
        name="^orders",
        use_regex=True,
        disable_stats=True,
    )

    assert result.page == 2
    assert result.page_count == 2
    assert [queue.name for queue in result.items] == ["orders.q"]
    assert dict(seen[0]) == {
        "disable_stats": "true",
        "name": "^orders",
        "page": "2",
        "page_size": "50",
        "use_regex": "true",
    }


@pytest.mark.integration
def test_exchange_list_all_fetches_every_page() -> None:
    seen_pages: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("page")
        seen_pages.append(page)
        if page == "1":
            return httpx.Response(200, json=_page([_exchange("first")], page=1, page_count=2, page_size=1))
        return httpx.Response(200, json=_page([_exchange("second")], page=2, page_count=2, page_size=1))

    manager = ExchangeManager(
        http_client=HttpxAdapter(host="localhost", port=15672, transport=httpx.MockTransport(handler)),
        vhost="%2F",
        strict=False,
    )

    exchanges = manager.list_all(page_size=1, disable_stats=True)

    assert [exchange.name for exchange in exchanges] == ["first", "second"]
    assert seen_pages == ["1", "2"]


@pytest.mark.integration
def test_get_with_disable_stats_sends_the_flag() -> None:
    seen: list[httpx.QueryParams] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params)
        return httpx.Response(200, json=_exchange("events"))

    manager = ExchangeManager(
        http_client=HttpxAdapter(host="localhost", port=15672, transport=httpx.MockTransport(handler)),
        vhost="policy",
        strict=False,
    )

    assert manager.get("events", disable_stats=True).name == "events"
    assert dict(seen[0]) == {"disable_stats": "true"}


@pytest.mark.integration
def test_no_stats_list_fixtures_accept_missing_policy() -> None:
    queues = json.loads((_FIXTURE_DIRECTORY / "queues-get-all-no-stats.json").read_text())
    exchanges = json.loads((_FIXTURE_DIRECTORY / "exchanges-get-all-no-stats.json").read_text())

    parsed_queues = [QueueResponse.model_validate(queue) for queue in queues]
    parsed_exchanges = [ExchangeResponse.model_validate(exchange) for exchange in exchanges]

    assert len(parsed_queues) == 13
    assert len(parsed_exchanges) == 76
    assert next(queue for queue in parsed_queues if queue.name == "orders.q").policy == "tie-dlx-b"
    assert next(exchange for exchange in parsed_exchanges if exchange.name == "events").policy == "tie-ae-b"
    assert next(exchange for exchange in parsed_exchanges if exchange.name == "ae.a").policy is None


@pytest.mark.integration
def test_policy_selections_normalize_response_models() -> None:
    queues = [
        QueueResponse.model_validate(
            {
                **_queue("orders.q"),
                "policy": "tie-dlx-b",
            }
        )
    ]
    exchanges = [ExchangeResponse.model_validate({**_exchange("events"), "policy": "tie-ae-b"})]

    selections = build_user_policy_selections(
        queues=queues,
        exchanges=exchanges,
        cluster_id="cluster-a",
    )

    queue_id = NodeId(cluster_id="cluster-a", vhost="/", name="orders.q", kind=NodeKind.QUEUE)
    exchange_id = NodeId(cluster_id="cluster-a", vhost="/", name="events", kind=NodeKind.EXCHANGE)

    assert selections[queue_id] == "tie-dlx-b"
    assert selections[exchange_id] == "tie-ae-b"
    with pytest.raises(TypeError):
        selections[queue_id] = "replacement"  # type: ignore[index]


@pytest.mark.integration
def test_policy_selections_reject_duplicate_resource_observations() -> None:
    queue = QueueResponse.model_validate({**_queue("orders.q"), "policy": "orders-policy"})

    with pytest.raises(TopologyValidationError, match="Duplicate user policy selections"):
        build_user_policy_selections(
            queues=[queue, queue],
            exchanges=[],
            cluster_id="cluster-a",
        )


@pytest.mark.integration
def test_policy_selections_reject_empty_policy_names() -> None:
    queue = QueueResponse.model_validate({**_queue("orders.q"), "policy": ""})

    with pytest.raises(TopologyValidationError, match="policy names must be non-empty"):
        build_user_policy_selections(
            queues=[queue],
            exchanges=[],
            cluster_id="cluster-a",
        )


@pytest.mark.integration
def test_captured_resource_snapshot_resolves_policy_ties_in_definitions_export() -> None:
    definitions = ClusterDefinitionsResponse.model_validate_json(
        (_FIXTURE_DIRECTORY / "full-export-definitions.json").read_text()
    )
    queues = [
        QueueResponse.model_validate(queue)
        for queue in json.loads((_FIXTURE_DIRECTORY / "queues-get-all-no-stats.json").read_text())
    ]
    exchanges = [
        ExchangeResponse.model_validate(exchange)
        for exchange in json.loads((_FIXTURE_DIRECTORY / "exchanges-get-all-no-stats.json").read_text())
    ]

    auditor = ClusterAuditor(
        definitions,
        queues=queues,
        exchanges=exchanges,
        in_cluster_amqp_hosts={"localhost"},
    )

    _assert_captured_full_export_audit(auditor)


@pytest.mark.integration
def test_from_files_uses_the_same_normalized_resource_inputs() -> None:
    auditor = ClusterAuditor.from_files(
        _FIXTURE_DIRECTORY / "full-export-definitions.json",
        queues_path=_FIXTURE_DIRECTORY / "queues-get-all-no-stats.json",
        exchanges_path=_FIXTURE_DIRECTORY / "exchanges-get-all-no-stats.json",
        in_cluster_amqp_hosts={"localhost"},
    )

    _assert_captured_full_export_audit(auditor)
