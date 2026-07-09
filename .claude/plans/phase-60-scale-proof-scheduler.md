# Phase 60 — 1M-row scale proof + scheduled scanning

## Why
The "millions of records" claim has never been EXECUTED (largest real run ≈ 6k rows). And nothing can
run scans on a schedule — a monitoring product must monitor. Two deliverables, both tools/-level.

HARD CONSTRAINTS: zero changes under `app/` (tools/ + tests/ + docs only; the proof exercises the
engine as a black box). No new REQUIRED deps (psutil may be used via guarded import with a ctypes
Windows fallback; absence → memory metric skipped with an honest note).

## A. Scale proof — `tools/scale_proof.py` (runnable: `python -m tools.scale_proof`)
1. `def generate(db_path: Path, rows: int, seed: int = 42) -> list[dict]` — SQLite:
   `owners(id INTEGER PRIMARY KEY, name TEXT NOT NULL)` (50 owners) and
   `deals(id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL REFERENCES owners(id),
   stage TEXT NOT NULL CHECK (stage IN ('open','won','lost')), amount REAL, email TEXT)`.
   Insert `rows` clean records in executemany chunks of 50k (bounded generation memory; seeded
   `random.Random`). PLANT 9 anomalies at three locations — ids ≈ 2, rows//2, rows-1 — at each
   location: one stage='zzz' (PRAGMA ignore_check_constraints=ON), one owner_id=999999 (dangling),
   one amount=9e9 (extreme). Use DISTINCT ids near each location (e.g. loc, loc+1, loc+2). Return the
   ground-truth list `[{"record_id", "family"}]` (families: picklist_invalid, orphan_reference,
   stat_outlier) and also write `truth.json`.
2. **Peak-memory sampler**: `class PeakRss` — background thread sampling every 0.2s. Prefer
   `psutil.Process().memory_info().rss`; on ImportError use ctypes
   `GetProcessMemoryInfo` (`PeakWorkingSetSize`) on Windows; else record None. `.start()/.stop()`
   returning peak bytes (or None).
3. `async def run_proof(workdir: Path, rows: int, seed: int = 42) -> dict`:
   - generate → `build_container(data_dir=workdir/'scanner', root=<repo root>)` → PeakRss.start() →
     `scan = await container.scan_service.stream_scan(SqliteTableSource(db_path=..., table_name=
     'deals', max_records=rows + 10))` → stop sampler. Time the scan with `time.monotonic()`.
   - Fetch findings for the scan; verify EVERY planted contract anomaly (picklist_invalid,
     orphan_reference ids) is present and each planted stat_outlier id is flagged by
     numeric_outlier or robust_outlier.
   - Return `{"rows", "row_count", "duration_s", "peak_rss_mb" (or None), "planted": n,
     "matched": n, "missing": [...], "finding_count", "batches" (parse from scan notes if easy else
     omit)}`.
4. `main()` argparse `--rows` (default 1_000_000) `--seed` `--out` (writes `scale-proof.json` +
   prints a compact summary line). Exit non-zero if any planted anomaly missed.

## B. Scheduled scanning — `tools/scheduled_scan.py` (runnable: `python -m tools.scheduled_scan`)
- argparse subcommands, each building a container against `SCANNER_DATA_DIR` (env, default ./data):
  - `org` — env SF creds (`session_from_environment`), flags `--max-objects --stream-threshold
    --api-budget`; calls `orchestrator_service.scan_org`; prints the summary counts.
  - `object --name Account [--max-records N] [--incremental]` — single Salesforce object via
    `SalesforceObjectSource`; `--incremental` routes through `sync_service.incremental_scan`.
  - `table --db path --table name [--stream]` — SQLite table via run_scan/stream_scan.
- Each run prints ONE final line `SCHEDULED_SCAN status=ok|error mode=<...> findings=<n>` and exits
  0/1 (Task Scheduler/cron friendly). Errors caught → stderr + exit 1. (Scan audit events already
  happen inside the services — do not duplicate.)
- `docs/scheduling.md`: how to schedule — Windows `schtasks /create /sc daily /tn ScannerOrgScan
  /tr "...python -m tools.scheduled_scan org"` example + cron line example + env-var checklist
  (SF creds, SCANNER_DATA_DIR, SCANNER_API_KEY note: CLI bypasses HTTP so no key needed).

## C. Tests — `tests/test_scale_proof.py` (NEW)
1. `test_scale_proof_smoke(tmp_path)` — `run_proof(tmp_path, rows=20_000)` (seconds, not minutes):
   completed scan, row_count == 20_000 (+/- planted rows — assert equals actual inserted count),
   matched == planted (ALL 9), duration recorded, peak_rss_mb is None-or-positive. ALWAYS runs in CI.
2. `test_full_million_proof` — guarded by `os.environ.get("RUN_SCALE_PROOF") != "1"` → pytest.skip.
   When enabled: rows=1_000_000, assert matched == planted and (peak_rss_mb is None or
   peak_rss_mb < 2000). (Claude will execute this separately in the background — do NOT run the 1M
   proof yourself; verify only the 20k smoke.)
3. `test_scheduled_scan_table(tmp_path)` — invoke the `table` subcommand via
   `tools.scheduled_scan.main(["table","--db",...,"--table","deals"])` on a small generated db (reuse
   generate with rows=500): exit code 0 and the SCHEDULED_SCAN status=ok line printed (capsys).

## D. Docs
`docs/scalability.md`: add a "## Measured proof" placeholder section that `tools/scale_proof.py
--out` numbers get pasted into (Claude will fill after the 1M run). README: one line for the two new
tools.

## Done criteria
`python -m pytest tests/test_scale_proof.py -q` (smoke + scheduler tests; 1M test skipped) and
`python -m pytest -q` green; nothing under app/ changed. Report files + tails + CONFIRM: (a) 20k
smoke matched 9/9 planted with duration + peak_rss; (b) scheduler table run exit 0; (c) git diff
clean under app/; (d) full-suite count.
