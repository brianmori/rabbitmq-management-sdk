"""Report degree facts about the captured configuration graph.

Each finding is exact for the supplied :class:`ClusterTopology`. A topology
edge records a configured possible message hop, not proof that a particular
message will traverse it. When supplied, resource observations resolve selected
regular policies but do not evaluate routing keys, headers, dead-letter
conditions, shovel runtime state, implicit default-exchange routing, or later
configuration changes.

An edge contributes to the degree of each endpoint present in the graph, even
when its other endpoint is absent. :meth:`ClusterTopology.dangling_edges`
reports that unresolved reference separately.
"""

from rabbitmq_management_sdk.topology.models import ClusterTopology, ExchangeNode, NodeId, QueueNode, ShovelNode
from rabbitmq_management_sdk.topology.ordering import node_sort_key


def _out_degree(topology: ClusterTopology) -> dict[NodeId, int]:
    """Count captured outgoing edges at each contained node."""
    counts: dict[NodeId, int] = dict.fromkeys(topology.all_node_ids, 0)
    for edge in topology.edges:
        if edge.source in counts:
            counts[edge.source] += 1
    return counts


def _in_degree(topology: ClusterTopology) -> dict[NodeId, int]:
    """Count captured incoming edges at each contained node."""
    counts: dict[NodeId, int] = dict.fromkeys(topology.all_node_ids, 0)
    for edge in topology.edges:
        if edge.target in counts:
            counts[edge.target] += 1
    return counts


def black_hole_exchanges(topology: ClusterTopology) -> tuple[ExchangeNode, ...]:
    """Return non-default exchanges with no captured outgoing hop.

    RabbitMQ's default exchange (the empty name) is excluded because its
    implicit queue hops do not appear as declared bindings. A result says
    exactly that the captured graph has no outgoing edge; it is not proof of
    message loss because a bindingless exchange may be an intentional discard
    point and the graph does not observe actual publications.
    """
    out_degree = _out_degree(topology)
    return tuple(
        sorted(
            (e for e in topology.exchanges if e.id.name != "" and out_degree[e.id] == 0),
            key=lambda e: node_sort_key(e.id),
        )
    )


def unreachable_internal_exchanges(topology: ClusterTopology) -> tuple[ExchangeNode, ...]:
    """Return internal exchanges with no captured incoming hop.

    Producers cannot publish directly to an internal exchange. This finding
    therefore means no captured edge can reach it. It does not assert that the
    configuration cannot change after capture or that the exchange was
    intended to receive messages.
    """
    in_degree = _in_degree(topology)
    return tuple(
        sorted(
            (e for e in topology.exchanges if e.internal and in_degree[e.id] == 0),
            key=lambda e: node_sort_key(e.id),
        )
    )


def queues_without_declared_ingress(topology: ClusterTopology) -> tuple[QueueNode, ...]:
    """Return queues with no captured inbound binding or local shovel edge.

    This is not an assertion that a queue cannot receive a message:
    RabbitMQ's default exchange can publish to every queue without an explicit
    binding in the definitions export.
    """
    in_degree = _in_degree(topology)
    return tuple(sorted((q for q in topology.queues if in_degree[q.id] == 0), key=lambda q: node_sort_key(q.id)))


def cross_vhost_shovels(topology: ClusterTopology) -> tuple[ShovelNode, ...]:
    """Return shovels whose resolved captured endpoint vhosts differ."""
    return tuple(sorted((s for s in topology.shovels if s.is_cross_vhost), key=lambda s: node_sort_key(s.id)))


def shovels_with_unresolved_vhost(topology: ClusterTopology) -> tuple[ShovelNode, ...]:
    """Return shovels where a captured endpoint vhost could not be determined.

    This is a separate fact from :func:`cross_vhost_shovels`, not a fuzzier
    version of it: the SDK does not know whether these cross a vhost boundary
    and says so rather than guessing.
    """
    return tuple(sorted((s for s in topology.shovels if s.is_cross_vhost is None), key=lambda s: node_sort_key(s.id)))


def shovels_with_unconfirmed_endpoints(topology: ClusterTopology) -> tuple[ShovelNode, ...]:
    """Return shovels with an endpoint not confirmed by local-host evidence.

    Hosted AMQP endpoints require positive evidence through
    ``parse_cluster_topology(..., in_cluster_amqp_hosts=...)``. An unconfirmed
    endpoint may be remote, unsupported, unresolved, or simply omitted from
    that evidence. Its resource edge is intentionally absent so same-named
    resources cannot be merged into a false cycle.
    """
    return tuple(sorted((s for s in topology.shovels if s.has_unconfirmed_endpoint), key=lambda s: node_sort_key(s.id)))
