from __future__ import annotations

import asyncio
import importlib
import os
import sqlite3
import time
from pathlib import Path

import httpx

from app.persistence.database import Database
from tests import harness


def _build_large_db(db_path: Path, n: int = 60_000) -> None:
    """Large clean items table (reuses the shape from tests/test_stream_detection.py, no anomalies needed)."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE items ("
            "id INTEGER PRIMARY KEY, "
            "kind TEXT NOT NULL CHECK (kind IN ('a','b')), "
            "note TEXT"
            ")"
        )
        rows = [("a" if i % 2 == 0 else "b", f"note-{i}") for i in range(n)]
        conn.executemany("INSERT INTO items (kind, note) VALUES (?, ?)", rows)
        conn.commit()
    finally:
        conn.close()


def _reload_app(data_dir: Path):
    """Reload app.main against a fresh data dir. httpx.ASGITransport does not run lifespan events,
    so replicate the one thing they do (schema setup) explicitly."""
    os.environ["SCANNER_DATA_DIR"] = str(data_dir)
    import app.main as main

    main = importlib.reload(main)
    main.app.state.container.database.setup()
    return main


def test_wal_enabled(tmp_path: Path):
    db = Database(tmp_path / "wal_test.db")
    with db.connect() as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert str(mode).lower() == "wal"


def test_health_responds_during_stream_scan(tmp_path: Path):
    db_path = tmp_path / "big.db"
    _build_large_db(db_path)
    file_bytes = db_path.read_bytes()
    main = _reload_app(tmp_path / "data")

    async def scenario():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            scan_done = asyncio.Event()
            scan_result: dict = {}

            async def run_scan():
                files = {"file": ("big.db", file_bytes, "application/octet-stream")}
                resp = await client.post(
                    "/api/scans/database/stream?table_name=items", files=files, timeout=120
                )
                scan_result["status_code"] = resp.status_code
                scan_result["body"] = resp.json()
                scan_done.set()

            scan_task = asyncio.create_task(run_scan())
            await asyncio.sleep(0.05)  # let the scan actually start on its worker thread

            t0 = time.monotonic()
            health_resp = await client.get("/api/health", timeout=3.0)
            elapsed = time.monotonic() - t0

            assert health_resp.status_code == 200
            assert health_resp.json()["status"] == "ok"
            assert not scan_done.is_set(), "scan finished before health check could run concurrently"

            await scan_task
            assert scan_result["status_code"] == 200, scan_result.get("body")
            assert scan_result["body"]["status"] == "completed"
            return elapsed

    elapsed = asyncio.run(scenario())
    assert elapsed < 3.0, f"/api/health took {elapsed:.2f}s (should stay live during the scan)"


def test_concurrent_read_during_stream_scan(tmp_path: Path):
    db_path = tmp_path / "big.db"
    _build_large_db(db_path)
    file_bytes = db_path.read_bytes()
    main = _reload_app(tmp_path / "data")

    async def scenario():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            scan_result: dict = {}
            read_result: dict = {}

            async def run_scan():
                files = {"file": ("big.db", file_bytes, "application/octet-stream")}
                resp = await client.post(
                    "/api/scans/database/stream?table_name=items", files=files, timeout=120
                )
                scan_result["status_code"] = resp.status_code
                scan_result["body"] = resp.json()

            async def run_read():
                resp = await client.get("/api/scans", timeout=10.0)
                read_result["status_code"] = resp.status_code
                read_result["text"] = resp.text

            scan_task = asyncio.create_task(run_scan())
            await asyncio.sleep(0.05)
            await run_read()
            await scan_task
            return scan_result, read_result

    scan_result, read_result = asyncio.run(scenario())
    assert read_result["status_code"] == 200, read_result.get("text")
    assert "database is locked" not in read_result["text"].lower()
    assert scan_result["status_code"] == 200
    assert scan_result["body"]["status"] == "completed"
