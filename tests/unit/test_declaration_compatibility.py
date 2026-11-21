"""Check captured binding flags independently of the queue declaration rule."""

import pickle

import pytest
from tests.shared.parser_fixtures import _binding, _exchange, _observed_queue, _queue, _response, _shovel_param, _vhost

from rabbitmq_management_sdk import ClusterAuditor
from rabbitmq_management_sdk.resources.v4.queues.schemas.queue_response import QueueResponse
from rabbitmq_management_sdk.topology import (
    BindingDurability,
    BindingDurabilityKind,
    EdgeKind,
    NodeId,
    NodeKind,
    QueueDeclarationCompatibility,
    QueueDeclarationStatus,
    edge_sort_key,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("target_kind", [NodeKind.QUEUE, NodeKind.EXCHANGE])
@pytest.mark.parametrize(
    ("source_durable", "target_durable", "classification"),
    [
        (True, True, BindingDurabilityKind.FULLY_DURABLE),
        (True, False, BindingDurabilityKind.SEMI_DURABLE),
        (False, True, BindingDurabilityKind.UNCLASSIFIED),
        (False, False, BindingDurabilityKind.FULLY_TRANSIENT),
    ],
)
def test_binding_durability(
    source_durable: bool, target_durable: bool, classification: BindingDurabilityKind, target_kind: NodeKind
) -> None:
    exchanges = [{**_exchange("entry", "v"), "durable": source_durable}]
    queues = []
    if target_kind == NodeKind.QUEUE:
        queues.append({**_queue("target", "v"), "durable": target_durable})
    else:
        exchanges.append({**_exchange("target", "v"), "durable": target_durable})
    auditor = ClusterAuditor(
        _response(
            global_parameters=[{"name": "internal_cluster_id", "value": "capture"}],
            vhosts=[_vhost("v")],
            queues=queues,
            exchanges=exchanges,
            bindings=[_binding("entry", "target", destination_type=target_kind.value)],
        )
    )

    (finding,) = auditor.binding_durability()

    assert isinstance(finding, BindingDurability)
    assert finding.source == NodeId("v", "entry", NodeKind.EXCHANGE, cluster_id="capture")
    assert finding.target == NodeId("v", "target", target_kind, cluster_id="capture")
    assert finding.source_durable is source_durable
    assert finding.target_durable is target_durable
    assert finding.classification is classification


def test_parallel_and_dangling_bindings_exclude_other_routes() -> None:
    auditor = ClusterAuditor(
        _response(
            vhosts=[_vhost("v"), _vhost("elsewhere")],
            exchanges=[_exchange("entry", "v", **{"alternate-exchange": "fallback"}), _exchange("fallback", "v")],
            queues=[_queue("work", "v", **{"x-dead-letter-exchange": "entry"}), _queue("missing", "elsewhere")],
            bindings=[
                _binding("entry", "work", "another-key"),
                _binding("entry", "work", arguments={"marker": "distinct"}),
                _binding("entry", "work"),
                _binding("entry", "missing"),
                _binding("absent", "work"),
                _binding("absent", "missing-exchange", destination_type="exchange"),
            ],
            parameters=[
                _shovel_param(
                    "copy",
                    "v",
                    **{
                        "src-uri": "amqp:///v",
                        "src-queue": "work",
                        "dest-uri": "amqp:///v",
                        "dest-exchange": "entry",
                    },
                )
            ],
        )
    )

    findings = auditor.binding_durability()

    assert len(findings) == 6
    assert tuple(item.binding for item in findings) == tuple(
        sorted((edge for edge in auditor.topology.edges if edge.kind == EdgeKind.BINDING), key=edge_sort_key)
    )
    assert {edge.kind for edge in auditor.topology.edges} == set(EdgeKind)
    assert all(item.source.name != "" for item in findings)
    assert {
        (item.binding.routing_key, item.binding.arguments)
        for item in findings
        if item.source.name == "entry" and item.target.name == "work"
    } == {("", None), ("", '{"marker": "distinct"}'), ("another-key", None)}
    assert {
        (item.source.name, item.target.name): (item.source_durable, item.target_durable)
        for item in findings
        if item.classification is BindingDurabilityKind.UNKNOWN
    } == {
        ("entry", "missing"): (True, None),
        ("absent", "work"): (None, True),
        ("absent", "missing-exchange"): (None, None),
    }


@pytest.mark.parametrize("version", ["4.2.9", "4.3.8"])
def test_queue_declaration_evidence_and_aggregate(version: str) -> None:
    def observed(name: str, vhost: str = "v", **extra: object) -> QueueResponse:
        fields = {"durable": False, "type": "classic", "arguments": {"x-queue-type": "classic"}, **extra}
        return _observed_queue(name, vhost, **fields)

    omitted = QueueResponse.model_validate(
        observed("omitted").model_dump(by_alias=True, exclude={"exclusive"}, exclude_unset=True)
    )
    auditor = ClusterAuditor(
        _response(
            rabbitmq_version=version,
            vhosts=[_vhost("v"), _vhost("elsewhere"), _vhost("plugin", "plugin-type")],
            queues=[
                *(
                    {**_queue(name, "v"), "durable": False, "auto_delete": True}
                    for name in ("shared", "omitted", "null", "mismatch")
                ),
                {**_queue("shared", "elsewhere"), "durable": False},
                {**_queue("no-observation", "v"), "durable": False, "exclusive": False},
                _queue("durable", "v"),
                {**_queue("quorum", "v", **{"x-queue-type": "quorum"}), "durable": False},
                {**_queue("stream", "v", **{"x-queue-type": "stream"}), "durable": False},
                {**_queue("plugin", "plugin"), "durable": False},
            ],
            exchanges=[_exchange("entry", "v")],
            bindings=[_binding("entry", "shared")],
        ),
        queues=[
            observed("shared", exclusive=False),
            observed("shared", "elsewhere", exclusive=True),
            observed("null", exclusive=None),
            observed("mismatch", durable=True, exclusive=False),
            observed("unexported", exclusive=True),
            omitted,
        ],
        exchanges=[],
    )

    compatibility = auditor.queue_declaration_compatibility()
    indeterminate = QueueDeclarationStatus.INDETERMINATE
    assert [(item.queue.vhost, item.queue.name, item.exclusive, item.status) for item in compatibility] == [
        ("elsewhere", "shared", True, QueueDeclarationStatus.NOT_AFFECTED),
        ("v", "mismatch", None, indeterminate),
        ("v", "no-observation", None, indeterminate),
        ("v", "null", None, indeterminate),
        ("v", "omitted", None, indeterminate),
        ("v", "shared", False, QueueDeclarationStatus.REJECTED_BY_DEFAULT),
    ]
    assert all(isinstance(item, QueueDeclarationCompatibility) for item in compatibility)
    assert all(item.target_version == "4.3" and item.assumes_default_settings for item in compatibility)
    assert compatibility[0].queue.kind is NodeKind.QUEUE

    restored = ClusterAuditor.from_topology(pickle.loads(pickle.dumps(auditor.topology)))
    assert restored.queue_declaration_compatibility() == compatibility
    assert restored.binding_durability() == auditor.binding_durability()
    report = restored.audit()
    assert report.queue_declaration_compatibility == compatibility
    assert report.binding_durability == auditor.binding_durability()
