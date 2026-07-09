"""1M-row scale proof: plant anomalies, stream-scan as black box, verify, report.

Hard constraint: tools/ + tests/ + docs only — engine exercised via
``app.api.container.build_container`` (no app/ changes).

Run: ``python -m tools.scale_proof --rows 1000000 --seed 42 --out scale-proof.json``
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any

from app.api.container import build_container
from app.ingestion.sqlite_source import SqliteTableSource

REPO_ROOT = Path(__file__).resolve().parents[1]

CONTRACT_TYPES: dict[str, str] = {
    "picklist_invalid": "picklist_invalid",
    "orphan_reference": "orphan_reference",
}
STAT_TYPES: frozenset[str] = frozenset({"numeric_outlier", "robust_outlier"})
CHUNK = 50_000


def generate(db_path: Path, rows: int, seed: int = 42) -> list[dict]:
    """Build SQLite owners+deals with ``rows`` clean records and 9 planted anomalies.

    Returns ground-truth list ``[{"record_id", "family"}]`` and writes ``truth.json``
    beside the database.
    """
    if rows < 9:
        raise ValueError(f"rows must be >= 9 to plant 9 anomalies; got {rows}")

    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    rnd = random.Random(seed)
    stages = ("open", "won", "lost")
    n_owners = 50

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE owners ("
            "id INTEGER PRIMARY KEY, "
            "name TEXT NOT NULL"
            ")"
        )
        conn.executemany(
            "INSERT INTO owners (id, name) VALUES (?, ?)",
            [(i, f"Owner {i}") for i in range(1, n_owners + 1)],
        )
        conn.execute(
            "CREATE TABLE deals ("
            "id INTEGER PRIMARY KEY, "
            "owner_id INTEGER NOT NULL REFERENCES owners(id), "
            "stage TEXT NOT NULL CHECK (stage IN ('open','won','lost')), "
            "amount REAL, "
            "email TEXT"
            ")"
        )

        for start in range(1, rows + 1, CHUNK):
            end = min(start + CHUNK - 1, rows)
            batch: list[tuple[Any, ...]] = []
            for i in range(start, end + 1):
                owner_id = ((i - 1) % n_owners) + 1
                stage = stages[(i - 1) % 3]
                amount = max(1.0, rnd.gauss(50_000.0, 2_000.0))
                email = f"user{i}@ex.com"
                batch.append((i, owner_id, stage, amount, email))
            conn.executemany(
                "INSERT INTO deals (id, owner_id, stage, amount, email) "
                "VALUES (?, ?, ?, ?, ?)",
                batch,
            )

        # Plant 9 anomalies at head / middle / tail (distinct ids near each location).
        locations = [2, rows // 2, rows - 1]
        truth: list[dict[str, Any]] = []
        used: set[int] = set()

        def _ids_near(loc: int) -> list[int]:
            candidates = [loc, loc + 1, loc + 2]
            if candidates[-1] > rows or candidates[0] < 1:
                candidates = [rows - 2, rows - 1, rows]
            out: list[int] = []
            for c in candidates:
                rid = c
                while rid in used or rid < 1 or rid > rows:
                    rid -= 1
                    if rid < 1:
                        rid = rows
                used.add(rid)
                out.append(rid)
            return out

        plants: list[tuple[int, str, str, Any]] = []
        for loc in locations:
            ids = _ids_near(loc)
            plants.append((ids[0], "picklist_invalid", "stage", "zzz"))
            plants.append((ids[1], "orphan_reference", "owner_id", 999999))
            plants.append((ids[2], "stat_outlier", "amount", 9e9))

        conn.execute("PRAGMA ignore_check_constraints=ON")
        for rid, family, field, value in plants:
            conn.execute(
                f'UPDATE deals SET "{field}" = ? WHERE id = ?',
                (value, rid),
            )
            truth.append({"record_id": str(rid), "family": family})
        conn.execute("PRAGMA ignore_check_constraints=OFF")
        conn.commit()
    finally:
        conn.close()

    truth_path = db_path.parent / "truth.json"
    truth_path.write_text(json.dumps(truth, indent=2), encoding="utf-8")
    return truth


class PeakRss:
    """Background thread sampling process RSS every 0.2s.

    Prefer psutil; on ImportError use ctypes GetProcessMemoryInfo on Windows;
    else peak remains None.
    """

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._peak: int | None = None
        self._sampler = self._build_sampler()

    @staticmethod
    def _build_sampler():
        try:
            import psutil  # type: ignore[import-untyped]

            proc = psutil.Process(os.getpid())

            def _psutil() -> int:
                return int(proc.memory_info().rss)

            return _psutil
        except ImportError:
            pass

        if sys.platform == "win32":
            try:
                import ctypes
                from ctypes import wintypes

                class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                    _fields_ = [
                        ("cb", wintypes.DWORD),
                        ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t),
                    ]

                GetCurrentProcess = ctypes.windll.kernel32.GetCurrentProcess
                GetProcessMemoryInfo = ctypes.windll.psapi.GetProcessMemoryInfo
                GetProcessMemoryInfo.argtypes = [
                    wintypes.HANDLE,
                    ctypes.POINTER(PROCESS_MEMORY_COUNTERS),
                    wintypes.DWORD,
                ]
                GetProcessMemoryInfo.restype = wintypes.BOOL

                def _ctypes() -> int | None:
                    counters = PROCESS_MEMORY_COUNTERS()
                    counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
                    ok = GetProcessMemoryInfo(
                        GetCurrentProcess(),
                        ctypes.byref(counters),
                        counters.cb,
                    )
                    if not ok:
                        return None
                    return int(counters.PeakWorkingSetSize)

                return _ctypes
            except Exception:
                pass

        return None

    def _loop(self) -> None:
        while not self._stop.wait(0.2):
            if self._sampler is None:
                continue
            try:
                val = self._sampler()
            except Exception:
                continue
            if val is None:
                continue
            if self._peak is None or val > self._peak:
                self._peak = int(val)

    def start(self) -> None:
        self._stop.clear()
        self._peak = None
        if self._sampler is not None:
            try:
                first = self._sampler()
                if first is not None:
                    self._peak = int(first)
            except Exception:
                pass
        self._thread = threading.Thread(target=self._loop, name="PeakRss", daemon=True)
        self._thread.start()

    def stop(self) -> int | None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._sampler is not None:
            try:
                last = self._sampler()
                if last is not None and (self._peak is None or last > self._peak):
                    self._peak = int(last)
            except Exception:
                pass
        return self._peak


def _verify(findings: list[dict], truth: list[dict]) -> tuple[int, list[dict]]:
    """Return (matched_count, missing_list)."""
    by_rec: dict[str, list[str]] = {}
    for f in findings:
        rid = str(f.get("record_id", ""))
        ftype = f.get("finding_type") or f.get("type") or ""
        by_rec.setdefault(rid, []).append(ftype)

    matched = 0
    missing: list[dict] = []
    for t in truth:
        rid = str(t["record_id"])
        family = t["family"]
        types = by_rec.get(rid, [])
        if family in CONTRACT_TYPES:
            want = CONTRACT_TYPES[family]
            ok = want in types
        elif family == "stat_outlier":
            ok = any(ft in STAT_TYPES for ft in types)
        else:
            ok = False
        if ok:
            matched += 1
        else:
            missing.append({"record_id": rid, "family": family, "found_types": types})
    return matched, missing


def _parse_batches(notes: str | None) -> int | None:
    if not notes:
        return None
    m = re.search(r"over\s+(\d+)\s+batches", notes)
    if m:
        return int(m.group(1))
    return None


async def run_proof(workdir: Path, rows: int, seed: int = 42) -> dict:
    """Generate dataset, stream-scan deals, verify planted anomalies, return metrics."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    db_path = workdir / "scale.db"
    truth = generate(db_path, rows, seed)

    container = build_container(data_dir=workdir / "scanner", root=REPO_ROOT)
    container.database.setup()

    sampler = PeakRss()
    sampler.start()
    t0 = time.monotonic()
    try:
        scan = await container.scan_service.stream_scan(
            SqliteTableSource(
                db_path=db_path,
                table_name="deals",
                max_records=rows + 10,
            )
        )
    finally:
        peak_bytes = sampler.stop()
    duration_s = time.monotonic() - t0

    findings = container.finding_service.list_findings(scan_id=scan["id"])
    matched, missing = _verify(findings, truth)
    peak_rss_mb: float | None
    if peak_bytes is None:
        peak_rss_mb = None
    else:
        peak_rss_mb = round(peak_bytes / (1024 * 1024), 2)

    result: dict[str, Any] = {
        "rows": rows,
        "row_count": scan.get("row_count"),
        "duration_s": round(duration_s, 3),
        "peak_rss_mb": peak_rss_mb,
        "planted": len(truth),
        "matched": matched,
        "missing": missing,
        "finding_count": scan.get("finding_count", len(findings)),
        "scan_id": scan.get("id"),
        "status": scan.get("status"),
    }
    batches = _parse_batches(scan.get("notes"))
    if batches is not None:
        result["batches"] = batches
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scale proof: plant anomalies, stream-scan, verify, write scale-proof.json"
    )
    parser.add_argument(
        "--rows", type=int, default=1_000_000, help="Row count (default 1000000)"
    )
    parser.add_argument("--seed", type=int, default=42, help="RNG seed (default 42)")
    parser.add_argument(
        "--out",
        type=str,
        default="scale-proof.json",
        help="Output JSON path (default scale-proof.json)",
    )
    args = parser.parse_args(argv)

    out = Path(args.out)
    if not out.is_absolute():
        out = Path.cwd() / out
    # Workdir = parent of out file (or cwd if bare filename in cwd)
    workdir = out.parent / "scale_proof_work"
    workdir.mkdir(parents=True, exist_ok=True)

    result = asyncio.run(run_proof(workdir, rows=args.rows, seed=args.seed))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print(
        f"SCALE_PROOF rows={result['rows']} row_count={result['row_count']} "
        f"duration_s={result['duration_s']} peak_rss_mb={result['peak_rss_mb']} "
        f"matched={result['matched']}/{result['planted']} "
        f"findings={result['finding_count']} missing={len(result['missing'])}"
    )
    if result["missing"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
