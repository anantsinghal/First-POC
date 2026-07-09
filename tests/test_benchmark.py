"""CI contract for accuracy benchmark (phase 59).

Contract families must hit perfect precision/recall on the in-memory path.
Statistical recall is a soft floor. Streaming asserts contract recall when scale
deps (duckdb/pyarrow) are present. Runtime kept small with rows=800.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tools.benchmark import CONTRACT_FAMILIES, run_benchmark


def test_benchmark_contract_accuracy(tmp_path: Path) -> None:
    result = asyncio.run(run_benchmark(tmp_path, rows=800, seed=1234))

    mem = result["in_memory"]
    for family in CONTRACT_FAMILIES:
        row = mem["per_family"][family]
        assert row["recall"] == 1.0, (
            f"in-memory {family} recall={row['recall']} matched={row['matched']}/{row['planted']}"
        )
        assert row["precision"] == 1.0, (
            f"in-memory {family} precision={row['precision']} "
            f"matched={row['matched']}/{row['planted']}"
        )

    stat = mem["per_family"]["stat_outlier"]
    assert stat["recall"] >= 0.8, (
        f"in-memory stat_outlier recall={stat['recall']} "
        f"matched={stat['matched']}/{stat['planted']}"
    )

    # Streaming half: only assert when scale deps available (duckdb + pyarrow).
    pytest.importorskip("duckdb")
    pytest.importorskip("pyarrow")
    stream = result["streaming"]
    for family in CONTRACT_FAMILIES:
        row = stream["per_family"][family]
        assert row["recall"] == 1.0, (
            f"streaming {family} recall={row['recall']} "
            f"matched={row['matched']}/{row['planted']}"
        )
