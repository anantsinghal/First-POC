# Phase 48 — Streaming tier-0 detection (bounded-memory DETECTING scan)

## Why
`/api/scans/profile` already streams (never materialises records) but runs NO detection. This phase
completes the scale story: a **detecting** scan that processes batches with bounded memory, running
the cheap per-record detectors (planner tier 0: schema-driven + rule interpreter) on each batch,
persisting findings per batch, and profiling out-of-core. Expensive detectors (stats/pattern/ML/graph)
are honestly reported as deferred — same language the planner already uses.

The natural first streaming source with a schema is the SQLite adapter (a big DB table).

HARD CONSTRAINTS: no changes under `app/detection/` except NONE AT ALL — the engine stays untouched
(tier-0 filtering already exists via `registry.detect(ctx, include=...)`). No new dependencies.
All existing tests stay green.

## 1. `app/ingestion/sqlite_source.py` — add streaming to `SqliteTableSource`
- New method `def iter_batches(self, batch_size: int = 5_000) -> Iterator[list[Record]]`:
  - Performs the same validation as `load()` (table-name regex, table exists), opens read-only,
    builds the same schema, then iterates the same SELECT with a cursor + `fetchmany(batch_size)`,
    yielding cleaned record batches (same cleaning + synthetic `Id` logic as `load()`).
  - Respects `max_records` overall; sets `self.truncated = True` when the cap was hit;
    sets `self.fieldnames` (same `["Id", *cols]` list) and `self.schema` (the built ObjectSchema)
    and `self.related_ids` (same parent-Id resolution as load(), computed BEFORE yielding batches —
    parent-id sets are small) as attributes after the first batch is prepared.
  - Refactor shared logic (`_build_schema`, cleaning row→record, related-ids resolution) into private
    helpers used by BOTH `load()` and `iter_batches()` — no behavior change to `load()`.
  - Close the connection when the generator is exhausted or closed (try/finally around the yield loop).

## 2. `app/services/scan_service.py` — `async def stream_scan(self, source) -> dict`
Bounded-memory DETECTING scan for sources that expose `iter_batches` (+ schema/fieldnames attrs):
1. `self.database.setup()`; scan_id/created as usual.
2. If the source lacks `iter_batches` → fall back to `await self.run_scan(source)` (one line).
3. Iterate `source.iter_batches()`. For the FIRST batch, read `source.fieldnames` / `source.schema` /
   `getattr(source, 'related_ids', None)`. Build the tier-0 include set ONCE:
   `plan = self.planner.plan(object_name, 0)` is wrong for this purpose — instead define tier-0
   explicitly from the planner: `tier0 = frozenset(d for d, t in DETECTOR_TIERS.items() if t == 0)`
   plus `"orphan_reference"` when related_ids is available (it is per-record given related_ids).
   Import `DETECTOR_TIERS` from `..planning.planner`.
4. For EACH batch: build `DetectionContext(object_name, batch, fields, self.policy, None,
   schema=schema, related_ids=related_ids or {})`; `candidates = self.registry.detect(ctx,
   include=tier0)`; open ONE db connection per batch (or hold one for the whole scan — hold ONE
   `with self.database.connect()` for the entire scan for atomicity) and `upsert_many` the batch's
   candidates. ALSO write the batch to an ephemeral Parquet via `write_record_batches`-style streaming
   writer (open a `pq.ParquetWriter` equivalent through a small generator plumbing: simplest is to
   collect NOTHING — instead, tee the batches: define an inner generator that yields each batch to the
   parquet writer while also detecting. Use `app/staging/parquet_store.write_record_batches(path,
   generator_that_also_detects(), columns)` where the generator does the detection+upsert side effects
   per batch before yielding it). Track `total_records`, `total_findings`, `batches`.
   If the scale deps are unavailable, skip the Parquet tee and count/detect only, then use the
   in-memory `Profiler` on an EMPTY list? No — in that case profile from accumulated per-field
   aggregates is out of scope: simply run WITHOUT a profile (store profile of empty records) and note
   it. Keep it simple: if `staging.is_available()` and duckdb available → columnar profile from the
   staged parquet then purge; else `profile = self.profiler.profile([], fields)` and add a note
   "profile skipped (scale deps unavailable)".
5. Coverage: `self.grader.grade(object_name, [], fields, record_count=total_records,
   sampled=getattr(source, 'truncated', False))`.
6. Persist: insert_running (row_count=total), coverage, complete with notes
   f"streamed scan: {total_findings} findings from tier-0 detectors over {batches} batches; "
   f"statistical/pattern/ML deferred (streaming)"; save_quality(observed=total_records,
   flagged=<distinct record ids flagged — track a set of candidate.record_id (ids only, bounded)>);
   save_schema when schema present; audit event "scan_streamed" with detail {row_count, batches,
   finding_count, deferred: ["numeric_outlier","robust_outlier","rare_combination","duplicate_name",
   "missing_children","pattern_break","ml_anomaly"]}.
7. Return `self.get_scan_detail(scan_id)`.
NOTE: keep the whole write path inside ONE `with self.database.connect() as conn:` block.

## 3. Endpoint — `app/api/routes_scans.py`
`POST /api/scans/database/stream` (params identical to `/api/scans/database`): same validation/temp
file handling, but calls `container.scan_service.stream_scan(source)`. Register nothing new in main
(same router).

## 4. Tests — `tests/test_stream_detection.py` (NEW)
Build a SQLite db in tmp_path: `items(id INTEGER PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN
('a','b')), owner_id INTEGER REFERENCES owners(id), note TEXT)` + `owners(id INTEGER PRIMARY KEY,
name TEXT)` with 2 owners. Insert 4_000 clean rows (kind alternating a/b, owner_id in {1,2}) plus,
via `PRAGMA ignore_check_constraints=ON`, one `kind='zzz'` row, one `kind=''` row, one
`owner_id=777` row (dangling).
1. **Streaming detects with bounded batches**: `src = SqliteTableSource(db_path=..., table_name=
   "items", max_records=10_000)`; monkey-verify batching by using `batch_size=500` — call
   `stream_scan` via a container-built ScanService (harness.run-scan-style but calling stream_scan
   with asyncio.run). Assert scan completed; findings include `picklist_invalid` (observed 'zzz'),
   `required_missing` (empty kind), `orphan_reference` (777); finding_count >= 3; notes contain
   "streamed scan"; audit has `scan_streamed` with batches > 1.
2. **Memory-boundedness proxy**: wrap `iter_batches` (subclass or monkeypatch) to record max batch
   len — assert max batch length == 500 (never the full 4k).
3. **Deferred honesty**: scan notes/audit detail list the deferred detector ids.
4. **API**: upload the .db to `/api/scans/database/stream?table_name=items` via harness.api_client →
   200, findings include the three types; bad table → 400.
5. **Fallback**: `stream_scan(CsvSource(...))`? CsvSource HAS iter_batches but no schema attr —
   handle: `getattr(source, 'schema', None)` None → tier0 minus schema-driven still includes
   rule_interpreter; simply assert stream_scan on the demo CsvSource completes and produces the
   rule-based findings (missing_contract_closed_won etc. fire per batch since rules are per-record).
   (CsvSource.iter_batches sets self.fieldnames; schema stays None.)

## 5. Docs
`docs/scalability.md`: mark "streaming/columnar DETECTION passes" done for tier-0 (stats/ML at scale
remain deferred by design via planner); one-line update in README API list (`POST
/api/scans/database/stream`).

## Done criteria
`python -m pytest -q` and the healthcheck green. Report files changed, test tails, and confirm:
(a) zero diffs under app/detection/, (b) streamed scan catches picklist_invalid + required_missing +
orphan_reference across batches, (c) max batch length stayed at batch_size.
