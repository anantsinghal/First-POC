# Phase 40 — Columnar staging + DuckDB profiler (bounded-memory profiling at scale)

## Why
To profile objects with millions of records WITHOUT loading them into memory, stage extracted records
to **Parquet** (columnar, on disk) and compute the profile with **DuckDB SQL** (exact min/max/mean/
median/distinct, out-of-core). This is the blueprint's prescribed scale substrate (doc 06). It produces
the SAME `Profile` shape as the in-memory `Profiler`, so it's a drop-in for large objects later. Fully
ADDITIVE + optional-dependency guarded (core app runs without duckdb/pyarrow; the in-memory path is
unchanged).

## 1. Optional deps — `pyproject.toml`
Add an extra (do NOT add to core `dependencies`): `scale = ["duckdb>=1.0", "pyarrow>=15"]`. (Both are
installed in this environment, so tests run.)

## 2. Parquet staging — `app/staging/__init__.py` (empty) + `app/staging/parquet_store.py`
- Guarded import: `try: import pyarrow as pa; import pyarrow.parquet as pq; _PYARROW = True; except
  Exception: _PYARROW = False`.
- `def is_available() -> bool`: return `_PYARROW`.
- `def write_records(path: str | Path, records: list[Record], columns: list[str]) -> None`: build a
  pyarrow Table where every column is `pa.string()`; each cell = `clean-ish` string value
  `str(rec.get(col, "") or "")` (records are already cleaned str dicts — keep as-is, missing -> "");
  write to Parquet at `path`.
- `def write_record_batches(path: str | Path, batches: Iterable[list[Record]], columns: list[str]) ->
  int`: stream-write using `pq.ParquetWriter` — for each batch build a `pa.RecordBatch`/Table (string
  columns) and `writer.write_table(...)`; never materialise all batches at once. Return total rows
  written. (This is the bounded-memory write path for huge extracts.)
- Raise a clear `RuntimeError("pyarrow not installed; install the 'scale' extra")` from the writers if
  `not _PYARROW`.

## 3. DuckDB profiler — `app/profiling/columnar_profiler.py`
- Guarded import duckdb: `_DUCKDB`.
- `class ColumnarProfiler`:
  - `def is_available(self) -> bool` (pyarrow not needed here; duckdb reads Parquet natively).
  - `def profile(self, parquet_path: str | Path, fields: list[str]) -> Profile`:
    - Open `con = duckdb.connect()`. `rel = "read_parquet('<path>')"`. `n = con.execute(f"SELECT
      COUNT(*) FROM {rel}").fetchone()[0]`.
    - For each `field` in `fields` (quote identifiers as `"field"` to handle names like `Contract__c`):
      - `non_empty = SELECT COUNT(*) WHERE trim("field") <> '' AND "field" IS NOT NULL`.
      - `null_rate = round(1 - non_empty/n, 3) if n else 0` (compute in Python from the two counts to
        match the in-memory rounding exactly).
      - `distinct_count = SELECT COUNT(DISTINCT "field") WHERE trim("field") <> '' AND "field" IS NOT
        NULL`.
      - `top_values`: `SELECT "field" AS v, COUNT(*) AS c FROM {rel} WHERE trim("field")<>'' GROUP BY v
        ORDER BY c DESC, v LIMIT 5` -> list of `(value, count)` tuples.
      - numeric: `cast_expr = TRY_CAST(REPLACE("field", ',', '') AS DOUBLE)`. `numeric_n = COUNT(cast)`;
        if `numeric_n >= 2`: compute `min(cast)`, `max(cast)`, `avg(cast)`, `median(cast)` in one query;
        build `numeric = {"min": <min as float>, "max": <max>, "mean": round(avg,2), "median":
        round(median,2)}` (DuckDB `median()` == quantile_cont(0.5), matching statistics.median). Else
        `numeric=None`.
      - Build `FieldProfile(null_rate, distinct_count, top_values, numeric)`.
    - Return `Profile(record_count=n, fields={...})`. Close the connection.
  - If `not _DUCKDB`, `profile(...)` raises `RuntimeError("duckdb not installed; install the 'scale'
    extra")`.

## 4. Tests — `tests/test_columnar_profiler.py` (NEW)
Top of file: `import pytest; pytest.importorskip("duckdb"); pytest.importorskip("pyarrow")`.
- **Parity with the in-memory profiler** (the correctness proof): load the 11-row demo via
  `tests.harness.demo_records()`. Write to a temp Parquet (`write_records`). Compute
  `ColumnarProfiler().profile(path, fields)` and `Profiler().profile(records, fields)`; assert:
  - `record_count` equal (11).
  - For every field: `distinct_count` equal; `null_rate` equal within `abs < 1e-9`; if one has a
    `numeric` block both do, and `min/max/mean/median` equal within `abs < 1e-6`.
  - `top_values` compared as a SET of `(value, count)` pairs (tie ordering may differ) — equal.
- **Scale / bounded memory**: build a generator that yields 20 batches of 10_000 synthetic records
  (e.g. `Id`, `Segment` in {Enterprise,SMB}, `Amount` numeric) = 200_000 rows; write via
  `write_record_batches` (never materialising all 200k at once); `profile(path, ["Id","Segment",
  "Amount"])` and assert `record_count == 200_000`, `Segment.distinct_count == 2`, and the `Amount`
  numeric min/max match the generated bounds. (Profiling reads from the file via DuckDB — the 200k rows
  are never held in a single Python list for the profile step.)

## 5. Docs — `docs/scalability.md`
Mark roadmap item "columnar profiling substrate (Parquet + DuckDB)" as ✅ done; add a one-line note that
the integration path is: streaming source → `write_record_batches` → `ColumnarProfiler` (and, later,
DuckDB SQL for aggregate/profile passes used by detectors) selected by the scan planner for large
objects.

## Constraints
- `from __future__ import annotations`; type hints; docstrings. Guarded imports so the core app + all
  existing tests run unchanged when duckdb/pyarrow are absent.
- Do NOT modify the in-memory `Profiler` or wire this into `scan_service` yet (substrate + parity proof
  only; integration is the next phase).

## Done criteria
- `python -m pytest -q` green (incl. the new columnar tests). Report files + results, confirm parity
  with the in-memory profiler on the demo and a 200k-row profile computed from Parquet via DuckDB.
