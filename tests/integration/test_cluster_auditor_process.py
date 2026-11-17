"""Verify safe public topology analysis across a spawned process boundary."""

import pickle
import subprocess
import sys
from typing import TYPE_CHECKING, cast

import pytest
from tests.shared.parser_fixtures import _exchange, _queue, _response, _vhost

from rabbitmq_management_sdk import ClusterAuditor

if TYPE_CHECKING:
    from rabbitmq_management_sdk.topology import (
        CycleSearchResult,
        DeadLetterCoverage,
        DirectDependencyInventory,
        ExchangeRoutingCoverage,
        QueueLimits,
        QueueTypeInventory,
        StronglyConnectedComponent,
    )

    type _AnalysisResults = tuple[
        tuple[StronglyConnectedComponent, ...],
        CycleSearchResult,
        QueueTypeInventory,
        tuple[DeadLetterCoverage, ...],
        tuple[ExchangeRoutingCoverage, ...],
        DirectDependencyInventory,
        tuple[QueueLimits, ...],
    ]

pytestmark = pytest.mark.integration

_WORKER_SCRIPT = """
import pickle
import sys

from rabbitmq_management_sdk import ClusterAuditor

topology = pickle.loads(sys.stdin.buffer.read())
auditor = ClusterAuditor.from_topology(topology)
result = (
    auditor.strongly_connected_components(),
    auditor.structural_cycles(),
    auditor.queue_type_inventory(),
    auditor.dead_letter_coverage(),
    auditor.exchange_routing_coverage(),
    auditor.direct_dependency_inventory(),
    auditor.queue_limits(),
)
sys.stdout.buffer.write(pickle.dumps(result, protocol=pickle.HIGHEST_PROTOCOL))
"""


def test_spawned_process_preserves_topology_analysis_results() -> None:
    """Canonical topology transfer must preserve analysis and vhost metadata."""
    definitions = _response(
        rabbitmq_version="4.3.6",
        vhosts=[_vhost("audit"), _vhost("empty", "quorum")],
        queues=[
            _queue("cycle-queue", "audit", **{"x-dead-letter-exchange": "cycle-exchange", "x-message-ttl": 1000}),
        ],
        exchanges=[_exchange("cycle-exchange", "audit")],
        bindings=[
            {
                "source": "cycle-exchange",
                "vhost": "audit",
                "destination": "cycle-queue",
                "destination_type": "queue",
                "routing_key": "",
                "arguments": {},
            }
        ],
    )
    source_auditor = ClusterAuditor(definitions)
    expected = (
        source_auditor.strongly_connected_components(),
        source_auditor.structural_cycles(),
        source_auditor.queue_type_inventory(),
        source_auditor.dead_letter_coverage(),
        source_auditor.exchange_routing_coverage(),
        source_auditor.direct_dependency_inventory(),
        source_auditor.queue_limits(),
    )

    completed = subprocess.run(
        [sys.executable, "-c", _WORKER_SCRIPT],
        input=pickle.dumps(source_auditor.topology, protocol=pickle.HIGHEST_PROTOCOL),
        capture_output=True,
        check=False,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
    actual = cast(
        "_AnalysisResults",
        pickle.loads(completed.stdout),
    )

    assert actual == expected
