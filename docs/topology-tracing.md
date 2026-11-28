# Trace configured topology paths

Given a captured queue, exchange, or shovel, follow configured edges upstream,
downstream, or both. For `Queue A → Shovel → Fanout exchange → Destination queue`,
a downstream depth of three includes the destination queue.

`ClusterAuditor.trace_topology(resource, direction=..., max_hops=...)` accepts
the full `NodeId` of a captured resource, a direction (`upstream`, `downstream`,
or `both`), and a positive hop limit. `None` allows any depth. Both directions
combines independent traversals from the same starting node. Invalid inputs
raise `TopologyAnalysisError`.

`TopologyTrace` returns ordered `node_ids` and exact `edges`, including parallel
bindings. Each direction has its own ends and cutoffs. Ends are captured nodes
with zero further edges in that direction. A cutoff has further edges outside
the result. Missing resources appear in `node_ids` as boundaries and can be
identified by checking `auditor.topology.all_node_ids`. A cycle can have no
end, and its edges are preserved within the requested depth.

The search works through `ClusterAuditor.from_topology()` and reports
configured connectivity independently of routing keys and runtime traffic.
