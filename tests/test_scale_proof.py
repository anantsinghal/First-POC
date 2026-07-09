"""Phase 60: scale-proof smoke + scheduled_scan table CLI.

The 20k smoke always runs. The 1M proof is SKIPPED unless RUN_SCALE_PROOF=1
(Claude executes the million-row run separately — do not force it here).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from tools.scale_proof import generate, run_proof
from tools import scheduled_scan


def test_scale_proof_smoke(tmp_path: Path) -> None:
    result = asyncio.run(run_proof(tmp_path, rows=20_000))
    assert result.get("status") in (None, "completed") or result.get("row_count") == 20_000
    assert result["row_count"] == 20_000
    assert result["matched"] == result["planted"] == 9
    assert result["duration_s"] is not None and result["duration_s"] >= 0
    peak = result["peak_rss_mb"]
    assert peak is None or peak > 0


@pytest.mark.skipif(
    os.environ.get("RUN_SCALE_PROOF") != "1",
    reason="set RUN_SCALE_PROOF=1 to run the 1M-row proof (long)",
)
def test_full_million_proof(tmp_path: Path) -> None:
    result = asyncio.run(run_proof(tmp_path, rows=1_000_000))
    assert result["matched"] == result["planted"]
    peak = result["peak_rss_mb"]
    assert peak is None or peak < 2000


def test_scheduled_scan_table(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_path = tmp_path / "sched.db"
    generate(db_path, rows=500, seed=42)
    # Isolate catalog under tmp_path so we do not touch the real data dir.
    os.environ["SCANNER_DATA_DIR"] = str(tmp_path / "scanner_data")
    code = scheduled_scan.main(
        ["table", "--db", str(db_path), "--table", "deals"]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert "SCHEDULED_SCAN status=ok" in captured.out
