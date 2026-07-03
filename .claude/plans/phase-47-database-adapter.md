# Phase 47 — Universal-core seam + SQLite database adapter (strategy Phase 3)

## Why
`docs/universal-strategy.md` defines the product as a contract-vs-behavior engine with pluggable
adapters. This phase proves it with a SECOND adapter: a **SQL database** (SQLite — stdlib, zero new
deps; PostgreSQL later follows the same shape using information_schema). Success criterion: the SAME
generic engine — zero changes to `app/detection/` — catches anomalies in database rows: invalid
CHECK-enum values via `picklist_invalid`, dangling foreign keys via `orphan_reference`, empty required
columns via `required_missing`. Also: move the Salesforce describe→schema translator OUT of the domain
layer into the adapter layer (clean seam).

HARD CONSTRAINTS
- Do NOT modify anything under `app/detection/`, `app/profiling/`, `app/coverage/`, `app/planning/`,
  `app/policy/`, `app/services/` (the engine must stay untouched — that IS the proof).
- No new dependencies (stdlib `sqlite3` only).
- All existing tests stay green; the only edits to existing test files are import-path updates for the
  moved translator (verify with grep, update every site).

## 1. Move the translator (clean seam)
- CREATE `app/ingestion/salesforce_schema.py`: move `schema_from_salesforce_describe` there VERBATIM
  from `app/domain/schema.py` (it takes a plain dict; imports only `app.domain.schema` types —
  correct dependency direction: adapter → domain).
- DELETE the function from `app/domain/schema.py` (keep `FieldType`, `FieldDescriptor`,
  `ObjectSchema`, `schema_from_dict` — those ARE the canonical model and stay in domain).
- `grep -rn "schema_from_salesforce_describe" app/ tests/` and update EVERY import site to
  `app.ingestion.salesforce_schema` (expected: `app/ingestion/salesforce_source.py`, plus test files
  such as `tests/test_schema_driven.py`, `tests/test_org_catalog.py` — trust grep, not this list).
  Import-line changes only; no behavior edits.

## 2. New adapter — `app/ingestion/sqlite_source.py` — `class SqliteTableSource(DataSource)`
`__init__(self, *, db_path: str | Path, table_name: str, max_records: int = 10_000,
resolve_references: bool = True)`.

Validation (do first in `load()`):
- `table_name` must match `^[A-Za-z_][A-Za-z0-9_]*$` else `IngestionError("Invalid table name",
  status_code=400)` (SQL-injection guard; same for referenced parent table names before querying them).
- Table must exist (`SELECT name FROM sqlite_master WHERE type='table' AND name=?`) else
  `IngestionError(f"Table '{table_name}' not found", status_code=400)`.
- Open the DB read-only: `sqlite3.connect(f"file:{path}?mode=ro", uri=True)`.

### Schema translation → canonical `ObjectSchema` (`_build_schema(conn) -> ObjectSchema`)
- `PRAGMA table_info("<table>")` → rows (cid, name, type, notnull, dflt_value, pk).
  Type mapping, case-insensitive substring on the declared type (SQLite affinity style):
  contains `INT`/`REAL`/`FLOA`/`DOUB`/`NUMER`/`DEC` → NUMBER; contains `BOOL` → BOOLEAN; contains
  `DATE` or `TIME` → DATE; else STRING.
  `required = (notnull == 1 and pk == 0)`. The pk column (pk == 1) → `FieldType.ID`.
- `PRAGMA foreign_key_list("<table>")` → rows with (table, from, to): the `from` column becomes
  `FieldType.REFERENCE` with `reference_to=(parent_table,)` (required stays as computed from notnull).
- CHECK-enum → picklist: read the table's CREATE sql from `sqlite_master`; regex
  `(?i)CHECK\s*\(\s*"?(\w+)"?\s+IN\s*\(([^)]+)\)\s*\)`; for each match, split the value list on commas,
  strip quotes/whitespace → that column becomes `FieldType.PICKLIST` with those
  `picklist_values` (unless it's the pk or a REFERENCE column).
- Synthetic `Id`: the engine identifies records by an `Id` field. If the pk column is not literally
  named `Id`, ADD a descriptor `FieldDescriptor(name="Id", data_type=FieldType.ID)` to the schema; if
  there is no pk at all, `Id` comes from SQLite `rowid`.

### `load() -> LoadedDataset`
- `SELECT * FROM "<table>" LIMIT ?` with `max_records + 1` (add `rowid` to the select when the table
  has no pk). `truncated = len(rows) > max_records`; keep only `max_records`.
- Records: every column value → `clean(str(v))` if `v is not None` else `""`. Add `"Id"` key = the pk
  column's value as a cleaned string (or rowid when no pk). `fields = ["Id", *original_columns]`
  (dedupe if a column is literally named Id).
- `related_ids`: when `resolve_references`, for each distinct FK parent table (name re-validated with
  the same regex): `SELECT "<to_col>" FROM "<parent>" LIMIT 100000` → `frozenset` of cleaned non-empty
  strings, keyed by the parent table name (must equal the `reference_to` value so
  `orphan_reference` matches).
- `source_name = f"sqlite:{Path(db_path).name}/{table_name}"`;
  `source_hash = stable_hash({"fields": fields, "records": records})` (import from `app.domain.models`).
- Return `LoadedDataset(object_name=table_name, source_name=..., fields=..., records=...,
  source_hash=..., schema=schema, related_ids=related_ids or None, truncated=truncated)`.
- Close the connection in a `finally`.

## 3. Endpoint — `app/api/routes_scans.py`
```
@router.post("/api/scans/database")
async def database_scan(table_name: str = Query(min_length=1, max_length=80),
                        file: UploadFile = File(...),
                        container: Container = Depends(get_container)) -> dict[str, Any]:
```
- Filename must end `.db`/`.sqlite`/`.sqlite3` else `HTTPException(400, "Upload a SQLite database
  file")`; `raw = await file.read()`; > 25_000_000 → `HTTPException(413, ...)`.
- Write bytes to a `tempfile.mkdtemp()` path; build `SqliteTableSource(db_path=..., table_name=...)`;
  `try: return await container.scan_service.run_scan(source)` with `except IngestionError → HTTPException(
  e.status_code, str(e))`; in a `finally`, delete the temp file + dir (best-effort).

## 4. Tests — `tests/test_sqlite_adapter.py` (NEW)
Build fixture DBs with stdlib sqlite3 in tmp_path. Seeding trick for violating data: dangling FKs
insert fine because SQLite leaves `PRAGMA foreign_keys` OFF by default; CHECK-violating rows are
seeded with `PRAGMA ignore_check_constraints = ON` before the INSERT.

Fixture: `customers(id INTEGER PRIMARY KEY, name TEXT NOT NULL)` with 3 rows (ids 1,2,3);
`orders(id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id),
status TEXT NOT NULL CHECK (status IN ('new','paid','shipped')), amount REAL)` with 12 rows:
10 clean (customer_id in 1..3, status valid, amounts ~100), plus one row with `customer_id=999`
(dangling) and one row with `status='bogus'` (via ignore_check_constraints) and one row with
`status=''` (empty string passes NOT NULL).

1. **Schema translation**: `SqliteTableSource(db_path, table_name="orders")` → `load()`; assert
   schema: `Id` present as ID; `customer_id` is REFERENCE reference_to ("customers",) and required;
   `status` is PICKLIST with values {'new','paid','shipped'} and required; `amount` is NUMBER;
   dataset fields start with "Id"; record values are cleaned strings.
2. **Same engine catches DB anomalies** (the money test): build
   `harness.context("orders", ds.records, fields=ds.fields, schema=ds.schema,
   related_ids=ds.related_ids)`; `harness.detect_all(...)`; assert
   `{"picklist_invalid", "orphan_reference", "required_missing"} <= set(type_counts)` and that the
   picklist finding's evidence observed_value == "bogus", the orphan's observed_value == "999".
   `harness.assert_healthy(findings)`.
3. **API end-to-end**: via `harness.api_client(tmp_path)`: POST the .db file to
   `/api/scans/database?table_name=orders` → 200, scan completed, `GET /api/findings?scan_id=` includes
   `picklist_invalid` and `orphan_reference`. Error paths: `table_name=missing_table` → 400;
   a `.txt` upload → 400; `table_name="orders; DROP"` → 400.
4. **Truncation honesty**: `SqliteTableSource(..., max_records=5)` → `truncated is True`; run through
   `harness.run_scan` → coverage grade "partial" and a "record cap" suppressed check.

## 5. Docs (small)
- `docs/universal-strategy.md` §7 audit: flip "Second adapter (database)" to ✅ (SQLite; PostgreSQL =
  same shape via information_schema, listed next) and "translator placement" to ✅ (moved to adapter).
- `README.md`: add `POST /api/scans/database?table_name=` to the API list and one line under "What it
  does" noting the engine also scans SQL databases (SQLite adapter; same detectors, zero engine change).

## Done criteria
Run `python -m pytest -q` (ALL green, no skips beyond the usual optional-dep skips) and
`python -m pytest tests/test_healthcheck.py -q`. Report: files created/changed/deleted, full test
output tail, and explicitly confirm (a) `app/detection/` has zero diffs (`git diff --stat` shows no
detection files), (b) the DB scan produced picklist_invalid + orphan_reference + required_missing.
