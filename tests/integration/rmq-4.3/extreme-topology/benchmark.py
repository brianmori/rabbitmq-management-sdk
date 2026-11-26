"""Time and check the RabbitMQ 4.3.8 extreme capture using the public SDK."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import TYPE_CHECKING

from rabbitmq_management_sdk import ClusterAuditor
from rabbitmq_management_sdk.topology import EdgeKind

if TYPE_CHECKING:
    from rabbitmq_management_sdk import TopologyAuditReport

CAPTURE = Path(__file__).resolve().parent
DENSE_CYCLES = 16_064  # Sum of 8! / ((8 - k)! * k) for k = 2 through 8.
STRUCTURAL_CYCLES = DENSE_CYCLES + 8 * 7 + 3 + 1
CANDIDATE_CYCLES = 8 * 3 + 3 + 1


def verify_capture() -> None:
    provenance = json.loads((CAPTURE / "provenance.json").read_bytes())
    for filename, expected in provenance["sha256"].items():
        assert hashlib.sha256((CAPTURE / filename).read_bytes()).hexdigest() == expected, filename


def verify_audit(report: TopologyAuditReport, cap: int) -> None:
    expected_structural = min(STRUCTURAL_CYCLES, cap) if cap else STRUCTURAL_CYCLES
    expected_candidates = min(CANDIDATE_CYCLES, cap) if cap else CANDIDATE_CYCLES
    assert len(report.structural_cycles.cycles) == expected_structural
    assert len(report.message_loop_candidates.cycles) == expected_candidates
    assert report.structural_cycles.truncated == (expected_structural < STRUCTURAL_CYCLES)
    assert report.message_loop_candidates.truncated == (expected_candidates < CANDIDATE_CYCLES)

    if not report.message_loop_candidates.truncated:
        candidate_lengths = Counter(len(cycle) for cycle in report.message_loop_candidates.cycles)
        assert candidate_lengths == {2: 8, 3: 8, 4: 9, 11: 1, 24: 1, 128: 1}
        signatures = Counter(
            frozenset(edge.kind for edge in cycle.edges) for cycle in report.message_loop_candidates.cycles
        )
        assert signatures == {
            frozenset({EdgeKind.BINDING, EdgeKind.DEAD_LETTER}): 17,
            frozenset({EdgeKind.BINDING, EdgeKind.DEAD_LETTER, EdgeKind.ALTERNATE_EXCHANGE}): 8,
            frozenset({EdgeKind.SHOVEL}): 1,
            frozenset({EdgeKind.SHOVEL, EdgeKind.BINDING}): 1,
            frozenset(EdgeKind): 1,
        }
    if not report.structural_cycles.truncated:
        dense_count = sum(
            all(node.name.startswith("00.dense.") for node in cycle.nodes) for cycle in report.structural_cycles.cycles
        )
        assert dense_count == DENSE_CYCLES
    assert len(report.cross_vhost_shovels) == 13
    assert len(report.competing_shovel_consumers) == 1
    assert {edge.target.name for edge in report.dangling_edges} == {"absent.ae", "absent.dlx"}
    hub = next(row for row in report.fanout_exchange_bindings if row.exchange.id.name == "hub.fanout")
    assert hub.queue_count == 96
    deep = next(row for row in report.exchange_routing_coverage if row.exchange.id.name == "deep.000")
    assert [target.name for target in deep.destinations] == ["deep.end"]


def run_once(cap: int, *, definitions_only: bool) -> tuple[float, float, float]:
    started = perf_counter()
    auditor = ClusterAuditor.from_files(
        CAPTURE / "full-export-definitions.json",
        queues_path=None if definitions_only else CAPTURE / "queues.json",
        exchanges_path=None if definitions_only else CAPTURE / "exchanges.json",
    )
    loaded = perf_counter()
    components = auditor.strongly_connected_components()
    partitioned = perf_counter()
    report = auditor.audit(max_cycles=cap)
    audited = perf_counter()

    topology = auditor.topology
    assert len(topology.nodes) == 5_000
    assert len(topology.edges) == 4_986
    assert Counter(node.queue_type for node in topology.queues) == {"classic": 2_900, "quorum": 96, "stream": 64}
    assert Counter(edge.kind for edge in topology.edges) == {
        EdgeKind.BINDING: 4_442,
        EdgeKind.ALTERNATE_EXCHANGE: 226,
        EdgeKind.DEAD_LETTER: 292,
        EdgeKind.SHOVEL: 26,
    }
    cyclic = [component for component in components if component.is_cyclic]
    assert len(cyclic) == 61
    assert max(len(component.nodes) for component in cyclic) == 128
    verify_audit(report, cap)
    return loaded - started, partitioned - loaded, audited - partitioned


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-cycles", type=int, default=256, help="Cycle cap for each result. Use 0 for all cycles.")
    parser.add_argument("--runs", type=int, default=3, help="Number of fresh loads and audits to time.")
    parser.add_argument("--definitions-only", action="store_true", help="Omit the queue and exchange observations.")
    options = parser.parse_args()
    if options.runs < 1 or options.max_cycles < 0:
        parser.error("--runs must be positive and --max-cycles must be nonnegative")

    verify_capture()
    timings = [run_once(options.max_cycles, definitions_only=options.definitions_only) for _ in range(options.runs)]
    print("Verified 5,000 nodes, 4,986 edges, 61 cyclic components, and 13 cross-vhost shovels.")
    names = ("Load and build graph", "SCC partition", "Full audit")
    for label, samples in zip(names, zip(*timings, strict=True), strict=True):
        values = ", ".join(f"{elapsed:.3f}s" for elapsed in samples)
        print(f"{label}: median {median(samples):.3f}s ({values})")


if __name__ == "__main__":
    main()
