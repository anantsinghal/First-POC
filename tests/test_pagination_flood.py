from __future__ import annotations

import asyncio
import random
import sqlite3
from pathlib import Path

import pytest

from app.api.container import build_container
from app.domain.enums import Severity
from app.domain.models import Candidate
from app.ingestion.sqlite_source import SqliteTableSource
from app.persistence.repositories import FindingRepository
from tests import harness


def _cand(rid: str) -> Candidate:
    return Candidate(
        object_name="Account", record_id=rid, record_name=rid, finding_type="missing_required",
        field_name="Name", severity=Severity.HIGH, confidence=0.9, priority=0.9,
        title="t", evidence={"why": "x"},
    )


# --- (a) Pagination: limit/offset + X-Total-Count, bare-list contract ---

def test_findings_pagination_limit_offset_and_total_header(tmp_path: Path):
    with harness.api_client(tmp_path) as client:
        container = build_container(data_dir=tmp_path, root=harness.ROOT)
        findings_repo = FindingRepository()
        cands = [_cand(f"00Q{i:04d}") for i in range(350)]
        with container.database.connect() as conn:
            findings_repo.upsert_many(
                conn, scan_id="scan-350", candidates=cands, source_hash="h",
                scan_created_at="2026-01-01T00:00:00+00:00", source_key="src:test",
            )

        resp = client.get("/api/findings?scan_id=scan-350&limit=100&offset=300")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)  # bare-list contract preserved
        assert len(body) == 50
        assert resp.headers["X-Total-Count"] == "350"

        default_resp = client.get("/api/findings?scan_id=scan-350")
        assert default_resp.status_code == 200
        assert len(default_resp.json()) == 200
        assert default_resp.headers["X-Total-Count"] == "350"

        # limit/offset bounds enforced (Query ge/le)
        assert client.get("/api/findings?limit=0").status_code == 422
        assert client.get("/api/findings?limit=1001").status_code == 422
        assert client.get("/api/findings?offset=-1").status_code == 422


# --- (b) ML flood control: top-K cap ---

def _build_ml_flood_db(db_path: Path, normal_n: int = 27_000, oddball_n: int = 3_000) -> None:
    """30k rows: a gaussian-ish normal cluster plus a scattered block of far-away multivariate
    oddballs, over two numeric columns so the Isolation Forest gets >=2 usable features. The
    Isolation Forest's contamination=0.02 flags ~2% of whatever population it scores regardless of
    how distinct the oddballs are, so row COUNT (not just the oddballs) is what pushes the total
    flagged count safely past the 500 cap (empirically ~650 flagged at this size)."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, amount REAL, score REAL)")
        rng = random.Random(1234)
        rows: list[tuple[float, float]] = []
        for _ in range(normal_n):
            rows.append((rng.gauss(100.0, 10.0), rng.gauss(50.0, 5.0)))
        for _ in range(oddball_n):
            rows.append((rng.gauss(100_000.0, 5_000.0), rng.gauss(90_000.0, 5_000.0)))
        conn.executemany("INSERT INTO items (amount, score) VALUES (?, ?)", rows)
        conn.commit()
    finally:
        conn.close()


def test_streamed_ml_findings_capped_and_reported(tmp_path: Path):
    pytest.importorskip("duckdb")
    pytest.importorskip("pyarrow")
    pytest.importorskip("sklearn")

    db_path = tmp_path / "ml_flood.db"
    _build_ml_flood_db(db_path)

    container = build_container(data_dir=tmp_path / "data", root=harness.ROOT)
    container.database.setup()
    src = SqliteTableSource(db_path=db_path, table_name="items", max_records=30_000)
    scan = asyncio.run(container.scan_service.stream_scan(src))
    assert scan["status"] == "completed"

    findings = container.finding_service.list_findings(scan_id=scan["id"])
    ml_findings = [f for f in findings if f["finding_type"] == "ml_anomaly"]
    assert len(ml_findings) <= 500, f"expected the 500-cap to hold, got {len(ml_findings)}"

    assert "ml candidates capped" in scan.get("notes", "")

    audits = container.audit_service.list(limit=20)
    streamed = [a for a in audits if a.get("event") == "scan_streamed" and a.get("scan_id") == scan["id"]]
    assert streamed, "missing scan_streamed audit"
    detail = streamed[0]["detail"]
    assert detail["ml_flagged_total"] > 500, "test fixture should flag well over the cap"
    assert detail["ml_flagged_total"] >= len(ml_findings)
    assert detail["ml_kept"] == len(ml_findings)


# --- (c) Far-fence: streamed IQR multiplier is wider than the in-memory detector's 1.5x ---

def _quartiles(values: list[float]) -> tuple[float, float]:
    duckdb = pytest.importorskip("duckdb")
    con = duckdb.connect()
    try:
        row = con.execute(
            "SELECT quantile_cont(x, 0.25), quantile_cont(x, 0.75) FROM (SELECT UNNEST(?) AS x)",
            [values],
        ).fetchone()
        return float(row[0]), float(row[1])
    finally:
        con.close()


def test_streamed_iqr_far_fence_multiplier(tmp_path: Path):
    pytest.importorskip("duckdb")
    pytest.importorskip("pyarrow")

    base_values = [float(100_000 + ((i % 11) - 5) * 100) for i in range(2000)]
    q1, q3 = _quartiles(base_values)
    iqr = q3 - q1
    # 2xIQR beyond Q3: inside the streamed pass's 3.0xIQR fence (must NOT flag) but well outside
    # the in-memory detector's 1.5xIQR fence, so this specifically exercises the widened multiplier.
    boundary_value = q3 + 2.0 * iqr
    extreme_value = 9e9

    db_path = tmp_path / "farfence.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE deals (id TEXT PRIMARY KEY, Amount REAL)")
        rows = [(f"N{i:04d}", v) for i, v in enumerate(base_values)]
        rows.append(("BOUNDARY", boundary_value))
        rows.append(("EXTREME", extreme_value))
        conn.executemany("INSERT INTO deals (id, Amount) VALUES (?, ?)", rows)
        conn.commit()
    finally:
        conn.close()

    container = build_container(data_dir=tmp_path / "data", root=harness.ROOT)
    container.database.setup()
    src = SqliteTableSource(db_path=db_path, table_name="deals", max_records=5000)
    scan = asyncio.run(container.scan_service.stream_scan(src))
    assert scan["status"] == "completed"

    findings = container.finding_service.list_findings(scan_id=scan["id"])
    numeric_ids = {
        f["record_id"] for f in findings
        if f["finding_type"] == "numeric_outlier"
        and "streamed" in str(f["evidence"].get("method", "")).lower()
    }
    assert "EXTREME" in numeric_ids
    assert "BOUNDARY" not in numeric_ids
