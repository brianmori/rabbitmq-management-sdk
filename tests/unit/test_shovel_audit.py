"""Tests for advisory groups of shovels sharing a source queue."""

from dataclasses import replace

import pytest

from rabbitmq_management_sdk.topology import (
    ClusterTopology,
    CompetingShovelConsumers,
    NodeId,
    NodeKind,
    QueueNode,
    ResourceEndpoint,
    ShovelNode,
)
from rabbitmq_management_sdk.topology.shovel_audit import competing_shovel_consumers

pytestmark = pytest.mark.unit


def _shovel(
    name: str,
    *,
    queue: str = "shared",
    source_vhost: str | None = "source",
    declaring_vhost: str = "control",
    confirmed: bool = True,
    kind: NodeKind = NodeKind.QUEUE,
) -> ShovelNode:
    source = ResourceEndpoint(
        protocol="local",
        authorities=(),
        vhost=source_vhost,
        resource_name=queue,
        resource_kind=kind,
        routing_key=None,
        is_confirmed_local=confirmed,
    )
    return ShovelNode(
        id=NodeId(cluster_id="captured", vhost=declaring_vhost, name=name, kind=NodeKind.SHOVEL),
        source=source,
        destination=replace(source, vhost="destination", resource_name="out", is_confirmed_local=False),
    )


def _topology(shovels: tuple[ShovelNode, ...], *, queues: tuple[QueueNode, ...] = ()) -> ClusterTopology:
    return ClusterTopology(
        cluster_id="captured",
        exchanges=frozenset(),
        queues=frozenset(queues),
        shovels=frozenset(shovels),
        edges=frozenset(),
    )


@pytest.mark.parametrize("queue_type", ["classic", "quorum", "plugin-queue"])
def test_group_contains_every_shovel_and_the_complete_source_identity(queue_type: str) -> None:
    shovels = (_shovel("third"), _shovel("first"), _shovel("second"))
    queue_id = NodeId(cluster_id="captured", vhost="source", name="shared", kind=NodeKind.QUEUE)
    queue = QueueNode(id=queue_id, queue_type=queue_type, durable=True)

    (group,) = competing_shovel_consumers(_topology(shovels, queues=(queue,)))

    assert isinstance(group, CompetingShovelConsumers)
    assert group.queue == queue_id
    assert [shovel.id.name for shovel in group.shovels] == ["first", "second", "third"]
    assert all(shovel.destination.is_confirmed_local is False for shovel in group.shovels)


@pytest.mark.parametrize("count", [0, 1])
def test_fewer_than_two_shovels_do_not_form_a_group(count: int) -> None:
    assert competing_shovel_consumers(_topology((_shovel("one"),)[:count])) == ()


def test_different_queues_and_source_vhosts_are_kept_separate() -> None:
    shovels = (
        _shovel("a", queue="one"),
        _shovel("b", queue="two"),
        _shovel("c", queue="one", source_vhost="other"),
    )

    assert competing_shovel_consumers(_topology(shovels)) == ()


def test_declaring_vhost_and_shovel_name_do_not_replace_source_queue_identity() -> None:
    shovels = (_shovel("same-name", declaring_vhost="z"), _shovel("same-name", declaring_vhost="a"))

    (group,) = competing_shovel_consumers(_topology(shovels))

    assert group.queue.vhost == "source"
    assert [shovel.id.vhost for shovel in group.shovels] == ["a", "z"]


@pytest.mark.parametrize("reverse", [False, True])
def test_groups_are_ordered_by_queue_identity(reverse: bool) -> None:
    shovels = tuple(
        _shovel(name, source_vhost=vhost, queue=queue, declaring_vhost=f"{vhost}/{queue}")
        for vhost, queue in (("z", "a"), ("a", "z"), ("a", "a"))
        for name in ("second", "first")
    )
    if reverse:
        shovels = shovels[::-1]

    groups = competing_shovel_consumers(_topology(shovels))

    assert [(group.queue.vhost, group.queue.name) for group in groups] == [("a", "a"), ("a", "z"), ("z", "a")]
    assert all([shovel.id.name for shovel in group.shovels] == ["first", "second"] for group in groups)


@pytest.mark.parametrize("confirmed", [False, True])
def test_exchange_sources_are_not_competing_queue_consumers(confirmed: bool) -> None:
    shovels = tuple(_shovel(name, kind=NodeKind.EXCHANGE, confirmed=confirmed) for name in ("one", "two"))

    assert competing_shovel_consumers(_topology(shovels)) == ()


@pytest.mark.parametrize("source_vhost", ["source", None])
def test_unconfirmed_sources_cannot_be_merged_with_a_local_queue(source_vhost: str | None) -> None:
    shovels = (
        _shovel("local"),
        _shovel("remote-one", confirmed=False, source_vhost=source_vhost),
        _shovel("remote-two", confirmed=False, source_vhost=source_vhost),
    )

    assert competing_shovel_consumers(_topology(shovels)) == ()


def test_a_source_without_a_fixed_resource_is_excluded() -> None:
    unresolved = _shovel("unresolved")
    unresolved = replace(unresolved, source=replace(unresolved.source, resource_name=None, resource_kind=None))

    assert competing_shovel_consumers(_topology((_shovel("local"), unresolved))) == ()


def test_a_shared_destination_does_not_form_a_source_group() -> None:
    shovels: tuple[ShovelNode, ...] = (_shovel("one", queue="first"), _shovel("two", queue="second"))
    shovels = tuple(replace(shovel, destination=replace(shovel.source, resource_name="shared")) for shovel in shovels)

    assert competing_shovel_consumers(_topology(shovels)) == ()


def test_known_stream_queue_is_excluded_without_hiding_an_ordinary_queue() -> None:
    stream_id = NodeId(cluster_id="captured", vhost="source", name="stream", kind=NodeKind.QUEUE)
    stream = QueueNode(id=stream_id, queue_type="stream", durable=True)
    shovels = tuple(
        _shovel(name, queue=queue, declaring_vhost=queue) for queue in ("stream", "ordinary") for name in ("one", "two")
    )

    (group,) = competing_shovel_consumers(_topology(shovels, queues=(stream,)))

    assert group.queue.name == "ordinary"


def test_missing_queue_declaration_preserves_shared_source_evidence() -> None:
    topology = _topology((_shovel("one"), _shovel("two")))

    (group,) = competing_shovel_consumers(topology)

    assert group.queue.name == "shared"
    assert group.queue not in topology.nodes_by_id
