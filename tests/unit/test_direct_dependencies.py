"""Test direct configuration references through the public audit facade."""

from dataclasses import replace

import pytest
from tests.shared.parser_fixtures import _binding, _exchange, _queue, _response, _shovel_param, _vhost

from rabbitmq_management_sdk import ClusterAuditor
from rabbitmq_management_sdk.topology import (
    DirectReference,
    DirectReferenceRole,
    EdgeKind,
    NodeId,
    NodeKind,
    ResourceDependencies,
    TopologyEdge,
    node_sort_key,
)

pytestmark = pytest.mark.unit


def test_exchange_lookup_explains_four_direct_relationships() -> None:
    auditor = ClusterAuditor(
        _response(
            global_parameters=[{"name": "internal_cluster_id", "value": "cluster-1"}],
            vhosts=[_vhost("orders"), _vhost("other")],
            queues=[
                _queue("work", "orders", **{"x-dead-letter-exchange": "orders.dlx", "x-dead-letter-routing-key": ""}),
                _queue("orders.dlx", "orders"),
                _queue("parking", "orders"),
            ],
            exchanges=[
                _exchange("orders.dlx", "orders"),
                _exchange("orders.dlx", "other"),
                _exchange("events", "orders", **{"alternate-exchange": "orders.dlx"}),
            ],
            bindings=[_binding("orders.dlx", "parking", "failed", vhost="orders")],
            parameters=[
                _shovel_param(
                    "archive",
                    "other",
                    **{
                        "src-uri": "amqp:///orders",
                        "src-exchange": "orders.dlx",
                        "src-exchange-key": "failed",
                        "dest-uri": "amqp://unconfirmed.example/orders",
                        "dest-exchange": "orders.dlx",
                    },
                )
            ],
        )
    )
    dlx = NodeId("orders", "orders.dlx", NodeKind.EXCHANGE, "cluster-1")
    parking = replace(dlx, name="parking", kind=NodeKind.QUEUE)
    shovel = replace(dlx, vhost="other", name="archive", kind=NodeKind.SHOVEL)
    expected = ResourceDependencies(
        dlx,
        True,
        (
            DirectReference(DirectReferenceRole.ALTERNATE_EXCHANGE, replace(dlx, name="events")),
            DirectReference(DirectReferenceRole.BINDING_SOURCE, parking, "failed"),
            DirectReference(DirectReferenceRole.DEAD_LETTER_EXCHANGE, replace(parking, name="work"), ""),
            DirectReference(DirectReferenceRole.SHOVEL_SOURCE, shovel, "failed"),
        ),
    )

    assert auditor.direct_dependencies(dlx) == expected
    assert auditor.direct_dependencies(parking) == ResourceDependencies(
        parking, True, (DirectReference(DirectReferenceRole.BINDING_TARGET, dlx, "failed"),)
    )
    for resource in (replace(dlx, vhost="other"), replace(dlx, kind=NodeKind.QUEUE)):
        assert auditor.direct_dependencies(resource) == ResourceDependencies(resource, True, ())
    assert auditor.direct_dependencies(replace(dlx, cluster_id="other-cluster")) is None
    assert auditor.direct_dependencies(replace(dlx, cluster_id=None)) is None
    assert auditor.direct_dependencies(shovel) is None


def test_inventory_preserves_parallel_bindings_and_dangling_endpoint_roles() -> None:
    auditor = ClusterAuditor(
        _response(
            vhosts=[_vhost("v")],
            queues=[_queue("isolated", "v"), _queue("work", "v", **{"x-dead-letter-exchange": "missing-dlx"})],
            exchanges=[_exchange("headers", "v", "headers", **{"alternate-exchange": "missing-alternate"})],
            bindings=[
                _binding("headers", "missing", "other-key", arguments={"color": "red"}),
                _binding("headers", "missing", arguments={"color": "red"}),
                _binding("headers", "missing", arguments={"color": "blue"}),
                _binding("missing-exchange", "headers", destination_type="exchange"),
                _binding("headers", "headers", "self", destination_type="exchange"),
            ],
        )
    )
    missing = NodeId("v", "missing", NodeKind.QUEUE)
    headers = NodeId("v", "headers", NodeKind.EXCHANGE)
    inventory = auditor.direct_dependency_inventory()
    row = inventory.by_resource[missing]
    assert not row.is_present
    assert row.references == (
        DirectReference(DirectReferenceRole.BINDING_TARGET, headers, "", '{"color": "blue"}'),
        DirectReference(DirectReferenceRole.BINDING_TARGET, headers, "", '{"color": "red"}'),
        DirectReference(DirectReferenceRole.BINDING_TARGET, headers, "other-key", '{"color": "red"}'),
    )
    assert inventory.by_resource[replace(headers, name="missing-exchange")].references == (
        DirectReference(DirectReferenceRole.BINDING_SOURCE, headers, ""),
    )
    headers_refs = inventory.by_resource[headers].references
    assert headers_refs == (
        DirectReference(DirectReferenceRole.BINDING_SOURCE, headers, "self"),
        *(replace(ref, role=DirectReferenceRole.BINDING_SOURCE, related=missing) for ref in row.references),
        DirectReference(DirectReferenceRole.BINDING_TARGET, headers, "self"),
        DirectReference(DirectReferenceRole.BINDING_TARGET, replace(headers, name="missing-exchange"), ""),
    )
    assert inventory.by_resource[replace(headers, name="missing-dlx")].references == (
        DirectReference(DirectReferenceRole.DEAD_LETTER_EXCHANGE, replace(missing, name="work")),
    )
    assert inventory.by_resource[replace(headers, name="missing-alternate")].references == (
        DirectReference(DirectReferenceRole.ALTERNATE_EXCHANGE, headers),
    )
    isolated = replace(missing, name="isolated")
    assert inventory.by_resource[isolated] == ResourceDependencies(isolated, True, ())
    assert auditor.direct_dependencies(replace(headers, name="")) is None
    assert auditor.direct_dependencies(replace(missing, name="unseen")) is None
    assert tuple(inventory.by_resource) == tuple(sorted(inventory.by_resource, key=node_sort_key))
    assert tuple(inventory.by_resource.values()) == inventory.resources
    with pytest.raises(TypeError):
        inventory.by_resource[isolated] = row  # type: ignore[index]


def test_shovel_roles_use_each_confirmed_local_endpoint_even_if_resource_is_missing() -> None:
    auditor = ClusterAuditor(
        _response(
            vhosts=[_vhost("v")],
            parameters=[
                _shovel_param(
                    "turnaround",
                    "control",
                    **{
                        "src-uri": "amqp:///v",
                        "src-exchange": "missing",
                        "src-exchange-key": "in",
                        "dest-uri": "amqp://known.example/v",
                        "dest-exchange": "missing",
                        "dest-exchange-key": "",
                    },
                )
            ],
        ),
        in_cluster_amqp_hosts={"known.example"},
    )
    exchange = NodeId("v", "missing", NodeKind.EXCHANGE)
    shovel = NodeId("control", "turnaround", NodeKind.SHOVEL)
    assert auditor.direct_dependencies(exchange) == ResourceDependencies(
        exchange,
        False,
        (
            DirectReference(DirectReferenceRole.SHOVEL_DESTINATION, shovel, ""),
            DirectReference(DirectReferenceRole.SHOVEL_SOURCE, shovel, "in"),
        ),
    )


def test_from_topology_uses_endpoint_evidence_for_shovels() -> None:
    auditor = ClusterAuditor(
        _response(
            vhosts=[_vhost("v")],
            queues=[_queue("q", "v")],
            parameters=[
                _shovel_param(
                    "copy",
                    "control",
                    **{
                        "src-uri": "amqp:///v",
                        "src-queue": "q",
                        "dest-uri": ["amqp:///v", "amqp://unconfirmed.example/v"],
                        "dest-queue": "q",
                    },
                )
            ],
        )
    )
    queue = NodeId("v", "q", NodeKind.QUEUE)
    shovel = NodeId("control", "copy", NodeKind.SHOVEL)
    # A manually supplied graph can omit a valid hop or contain a contradictory one.
    topology = replace(auditor.topology, edges=frozenset({TopologyEdge(shovel, queue, EdgeKind.SHOVEL)}))
    expected = ResourceDependencies(queue, True, (DirectReference(DirectReferenceRole.SHOVEL_SOURCE, shovel),))
    assert auditor.direct_dependencies(queue) == expected
    assert ClusterAuditor.from_topology(topology).direct_dependencies(queue) == expected


def test_referenced_default_exchange_does_not_create_implicit_bindings() -> None:
    auditor = ClusterAuditor(
        _response(
            vhosts=[_vhost("v")],
            queues=[_queue("work", "v", **{"x-dead-letter-exchange": ""}), _queue("untouched", "v")],
        )
    )
    exchange = NodeId("v", "", NodeKind.EXCHANGE)
    work = NodeId("v", "work", NodeKind.QUEUE)
    untouched = replace(work, name="untouched")

    assert auditor.direct_dependencies(exchange) == ResourceDependencies(
        exchange, True, (DirectReference(DirectReferenceRole.DEAD_LETTER_EXCHANGE, work),)
    )
    assert auditor.direct_dependencies(work) == ResourceDependencies(work, True, ())
    assert auditor.direct_dependencies(untouched) == ResourceDependencies(untouched, True, ())
