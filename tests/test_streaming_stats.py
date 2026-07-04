from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from app.api.container import build_container
from app.detection.statistical.thresholds import MAD_THRESHOLD, mad_z, iqr_fences
from app.ingestion.sqlite_source import SqliteTableSource
from tests import harness


def _build_streaming_stats_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE deals ("
            "id TEXT PRIMARY KEY, "
            "Name TEXT, "
            "Amount REAL, "
            "Score REAL, "
            "Segment TEXT, "
            "Region TEXT, "
            "CloseDate DATE"
            ")"
        )
        rows = []
        for i in range(3997):
            rows.append(
                (
                    f"N{i:04d}",
                    f"Deal {i}",
                    float(100000 + ((i % 11) - 5) * 100),
                    float(50 + (i % 7)),
                    "SMB" if i % 2 else "Enterprise",
                    "North" if i % 3 else "South",
                    f"2026-07-{(i % 28) + 1:02d}",
                )
            )
        rows.extend(
            [
                ("X1", "Oddball 1", 9_000_000.0, 999.0, "Enterprise", "West", "2035-12-31"),
                ("X2", "Oddball 2", 5.0, -100.0, "SMB", "West", "2010-01-01"),
                ("X3", "Oddball 3", 250_000.0, 400.0, "Enterprise", "West", "2034-06-30"),
            ]
        )
        conn.executemany(
            "INSERT INTO deals (id, Name, Amount, Score, Segment, Region, CloseDate) VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def _stream_source(db_path: Path) -> SqliteTableSource:
    src = SqliteTableSource(db_path=db_path, table_name="deals", max_records=10_000)
    original_iter = src.iter_batches

    def force_small(batch_size=5000):
        return original_iter(batch_size=500)

    src.iter_batches = force_small
    return src


def test_threshold_extraction_hand_checks() -> None:
    assert iqr_fences(10.0, 20.0) == (-5.0, 35.0)
    assert iqr_fences(10.0, 10.0) is None
    assert round(mad_z(20.0, 10.0, 2.0), 4) == 3.3725
    assert MAD_THRESHOLD == 3.5


def test_streamed_stats_catch_planted_outliers(tmp_path: Path) -> None:
    pytest.importorskip("duckdb")
    pytest.importorskip("pyarrow")

    db_path = tmp_path / "streaming_stats.db"
    _build_streaming_stats_db(db_path)
    container = build_container(data_dir=tmp_path / "stream", root=harness.ROOT)
    container.database.setup()

    scan = asyncio.run(container.scan_service.stream_scan(_stream_source(db_path)))
    findings = container.finding_service.list_findings(scan_id=scan["id"])

    numeric = [
        finding for finding in findings
        if finding["finding_type"] == "numeric_outlier"
        and "streamed" in str(finding["evidence"].get("method", "")).lower()
    ]
    robust = [
        finding for finding in findings
        if finding["finding_type"] == "robust_outlier"
        and "streamed" in str(finding["evidence"].get("method", "")).lower()
    ]

    assert {finding["record_id"] for finding in numeric} >= {"X1"}
    assert {finding["record_id"] for finding in robust} >= {"X1"}
    assert "streamed stats" in scan["notes"]


def test_streamed_mad_matches_in_memory_ids(tmp_path: Path) -> None:
    pytest.importorskip("duckdb")
    pytest.importorskip("pyarrow")

    db_path = tmp_path / "equivalence.db"
    _build_streaming_stats_db(db_path)

    stream_container = build_container(data_dir=tmp_path / "streamed", root=harness.ROOT)
    stream_container.database.setup()
    streamed_scan = asyncio.run(stream_container.scan_service.stream_scan(_stream_source(db_path)))
    streamed_findings = stream_container.finding_service.list_findings(scan_id=streamed_scan["id"])

    load_source = SqliteTableSource(db_path=db_path, table_name="deals", max_records=10_000)
    dataset = asyncio.run(load_source.load())
    in_memory_scan, in_memory_findings = harness.run_scan(dataset, tmp_path / "memory")

    streamed_robust_ids = {
        finding["record_id"] for finding in streamed_findings if finding["finding_type"] == "robust_outlier"
    }
    in_memory_robust_ids = {
        finding["record_id"] for finding in in_memory_findings if finding["finding_type"] == "robust_outlier"
    }
    streamed_numeric_ids = {
        finding["record_id"] for finding in streamed_findings if finding["finding_type"] == "numeric_outlier"
    }
    in_memory_numeric_ids = {
        finding["record_id"] for finding in in_memory_findings if finding["finding_type"] == "numeric_outlier"
    }

    assert streamed_robust_ids == in_memory_robust_ids
    assert "X1" in streamed_robust_ids
    assert "X1" in streamed_numeric_ids
    assert "X1" in in_memory_numeric_ids
    assert in_memory_scan["status"] == "completed"


def test_streamed_sampled_ml_is_present_and_deterministic(tmp_path: Path) -> None:
    pytest.importorskip("duckdb")
    pytest.importorskip("pyarrow")
    pytest.importorskip("sklearn")

    db_path = tmp_path / "ml_streaming.db"
    _build_streaming_stats_db(db_path)

    def run_once(data_dir: Path) -> tuple[dict, list[dict]]:
        container = build_container(data_dir=data_dir, root=harness.ROOT)
        container.database.setup()
        scan = asyncio.run(container.scan_service.stream_scan(_stream_source(db_path)))
        findings = container.finding_service.list_findings(scan_id=scan["id"])
        return scan, findings

    first_scan, first_findings = run_once(tmp_path / "ml-run-1")
    second_scan, second_findings = run_once(tmp_path / "ml-run-2")

    first_ids = sorted(
        finding["record_id"]
        for finding in first_findings
        if finding["finding_type"] == "ml_anomaly"
        and "sampled" in str(finding["evidence"].get("method", "")).lower()
    )
    second_ids = sorted(
        finding["record_id"]
        for finding in second_findings
        if finding["finding_type"] == "ml_anomaly"
        and "sampled" in str(finding["evidence"].get("method", "")).lower()
    )

    assert first_ids
    assert first_ids == second_ids
    assert "X1" in first_ids or "X2" in first_ids or "X3" in first_ids
    assert "sampled ML" in first_scan["notes"]
    assert "sampled ML" in second_scan["notes"]


def test_stream_scan_columnar_fallback_regression(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "fallback.db"
    _build_streaming_stats_db(db_path)

    container = build_container(data_dir=tmp_path / "fallback-run", root=harness.ROOT)
    container.database.setup()
    monkeypatch.setattr(container.scan_service, "_columnar_available", lambda: False)

    scan = asyncio.run(container.scan_service.stream_scan(_stream_source(db_path)))
    findings = container.finding_service.list_findings(scan_id=scan["id"])
    audits = container.audit_service.list(limit=10)
    streamed = [entry for entry in audits if entry["event"] == "scan_streamed" and entry["scan_id"] == scan["id"]]

    assert scan["status"] == "completed"
    assert "profile skipped (scale deps unavailable)" in scan["notes"]
    assert "deferred" in scan["notes"].lower()
    assert all(finding["finding_type"] != "ml_anomaly" for finding in findings)
    assert streamed
    assert "numeric_outlier" in streamed[0]["detail"]["deferred"]
    assert "ml_anomaly" in streamed[0]["detail"]["deferred"]
