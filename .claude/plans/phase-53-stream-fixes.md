# Phase 53 — Fix the three live-testing defects (bulk memory, checkpointing, schemeless URLs)

Three defects found in real-org testing / code review. All fixes verified by tests.

HARD CONSTRAINTS: no changes under `app/detection/` EXCEPT `app/detection/schema_driven/
format_validity.py` (defect C is in that detector). No new dependencies. Only the explicitly listed
test updates; everything else stays green.

## A. Bulk-path memory bug — stream through Parquet, never accumulate
**Bug (app/services/scan_service.py, stream_scan):** the async (`aiter_batches`) branch does
`detected_batches.append(...)` for EVERY batch and only then calls `write_record_batches` — so a
5M-row Bulk pull holds all rows in RAM, defeating the bounded-memory design. The sync branch uses a
true generator and is fine.

**Fix:**
1. `app/staging/parquet_store.py`: add `class BatchParquetWriter` — `__init__(self, path, columns)`
   opens a `pq.ParquetWriter` with the all-string schema (same as `write_record_batches` uses);
   `def write(self, records: list[Record]) -> None` writes one batch as a table; `def close(self)`;
   context-manager support (`__enter__`/`__exit__`). Guarded by the existing `_PYARROW` flag with the
   same RuntimeError message. REFACTOR `write_record_batches` to use `BatchParquetWriter` internally
   (behavior identical, return total rows).
2. `stream_scan`: replace BOTH branches' Parquet handling with one `BatchParquetWriter` used inside
   the per-batch loop — `writer.write(batch)` right after `_process_batch(batch)`, for sync AND async
   paths. Delete the `detected_batches` list entirely. `writer.close()` before profiling.

## B. Per-batch checkpointing — crash-resilient persistence
**Bug:** all candidates accumulate in `collected_candidates` and are persisted once at the very end;
a crash at batch 900/1000 loses everything (and the manifest never exists).

**Fix (restructure stream_scan's write path):**
1. `app/persistence/repositories.py`: add `ScanRepository.set_row_count(conn, scan_id, row_count)` —
   `UPDATE scan_manifests SET row_count=? WHERE id=?`.
2. `stream_scan`: open the single `self.database.connect()` BEFORE consuming batches.
   `insert_running(...)` immediately with `row_count=0` (docstring: row_count is finalized at
   completion). Then per batch, inside `_process_batch`: `findings.upsert_many(conn, ...)` with just
   that batch's candidates, then `conn.commit()` (explicit checkpoint — the context manager still
   commits/closes at the end as today). Drop `collected_candidates`; keep `flagged` (ids only) and
   the counters. At the end (same connection): coverage insert, `set_row_count(scan_id, total)`,
   `complete(...)`, `save_quality`, `save_schema`, audit (add `"checkpointed": True` to the detail).
   Crash semantics (document in the docstring): on crash mid-scan the manifest exists with status
   'running' and all findings from completed batches are already persisted.
   NOTE the connect() contextmanager yields the conn — calling `conn.commit()` mid-stream is safe.

## C. Schemeless URLs are valid — kill the last false-positive class
**Defect:** real orgs store `Website` values like `www.pyramid.com` / `dickenson-consulting.com`
(no `http://`). Extremely common, harmless. Currently flagged `invalid_format` (8 of 27 findings on a
real Account scan were this).

**Fix (`app/detection/schema_driven/format_validity.py`):** add
`DOMAIN_RE = re.compile(r"^(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,}(?:/[^\s]*)?$")`
and extend `_is_valid_url` to accept: absolute http(s) (URL_RE) OR root-relative path
(RELATIVE_PATH_RE) OR schemeless domain (DOMAIN_RE). Update the module comment: found via real-org
testing 2026-07-04. Values like `nope`, `not a url` (no dot / whitespace) stay invalid.

**Consequence — ONE existing test updates (explicitly allowed):** in
`tests/test_format_validity.py::test_format_validity_contact_cases` the "invalid" Website case is
`"x.com"`, which is structurally a valid schemeless domain under the new (correct) policy. Change that
record's Website to `"nope"` (still yields exactly 1 invalid Website finding; same assertions).
No other existing test uses a dotted schemeless domain as an invalid case (verify with grep; `"nope"`
in test_salesforce_scan/test_healthcheck stays invalid since it has no dot).

## D. Tests — `tests/test_stream_fixes.py` (NEW)
1. **BatchParquetWriter**: importorskip pyarrow+duckdb. Write 3 batches of 100 string-records via the
   class (context manager), then `ColumnarProfiler().profile(path, cols)` → record_count == 300.
2. **Async bulk path streams (no accumulation)**: monkeypatch/spy `BatchParquetWriter.write` to count
   calls; run the existing bulk mock fixture (reuse the pattern from tests/test_bulk_api.py: describe +
   jobs/query lifecycle + 2 CSV result pages) through `stream_scan` with a small `batch_size` so there
   are >= 2 batches; assert `write` was called once PER BATCH (call_count == batches from the scan
   notes/audit) — proving per-batch teeing, not end-accumulation.
3. **Checkpointing**: build a SQLite fixture table with ~2000 rows (reuse SqliteTableSource); wrap
   `iter_batches` in a generator that raises RuntimeError after yielding the first 2 batches
   (batch_size=500). Call `stream_scan` and swallow the RuntimeError (pytest.raises). Then assert
   directly against the DB: the scan manifest EXISTS with status 'running' AND findings from the first
   1000 rows are persisted (count > 0 via FindingRepository.count_for_scan). Then run a clean
   full `stream_scan` on the same source succeeds (fresh scan id).
4. **Schemeless URLs**: `www.pyramid.com`, `dickenson-consulting.com`, `sub.example.co/path` valid
   (no findings); `nope`, `not a url`, `http://` invalid. Root-relative + absolute still valid
   (regression).

## E. Docs
`docs/scalability.md`: update the integration note — bulk/stream path is now truly bounded-memory
with per-batch finding checkpoints.

## Done criteria
`python -m pytest -q` and `python -m pytest tests/test_healthcheck.py -q` green. Report: files
changed, both tails, confirmation that (a) no `detected_batches` accumulation remains in stream_scan,
(b) the checkpoint test shows persisted findings after a mid-scan crash, (c) www-style domains no
longer flagged.
