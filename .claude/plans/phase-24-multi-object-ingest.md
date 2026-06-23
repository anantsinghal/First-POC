# Phase 24 — Multi-object scan ingestion (make the relationship layer usable)

Add a way to run a scan over a PRIMARY object plus its RELATED objects, so the relationship detectors
(`missing_children`, and `orphan_reference` when a schema is present) fire on real uploaded data
instead of only in tests. Offline path = multiple CSVs in one request. ADDITIVE — existing endpoints,
sources, and tests are unchanged.

## 1. Shared CSV parse helper — `app/ingestion/csv_source.py`
Factor the CSV parse into a module-level function so both sources share it (behavior identical to
today):
```
def parse_csv_bytes(raw_bytes: bytes) -> tuple[list[str], list[Record]]:
    """Decode utf-8-sig, csv.DictReader, return (fields, cleaned records). Raise IngestionError(400) on error."""
```
Refactor `CsvSource.load()` to call `parse_csv_bytes(self.raw_bytes)` (then compute source_hash +
build LoadedDataset as before). Keep `CsvSource`'s public behavior exactly the same (test_ingestion
must still pass).

## 2. New source — `app/ingestion/multi_csv_source.py` — `class MultiCsvSource(DataSource)`
- `__init__(self, *, primary_object: str, files: list[tuple[str, bytes]], source_name: str)` where each
  `files` item is `(object_name, raw_bytes)`.
- `async def load(self) -> LoadedDataset`:
  - Parse every file via `parse_csv_bytes`. Build `parsed: dict[str, tuple[fields, records]]` keyed by
    object_name (case-sensitive API names).
  - If `self.primary_object not in parsed`: raise `IngestionError(f"Primary object '{self.primary_object}'
    was not among the uploaded files.", status_code=400)`.
  - `primary_fields, primary_records = parsed[self.primary_object]`.
  - `related_records = {obj: recs for obj, (flds, recs) in parsed.items() if obj != self.primary_object}`.
  - `source_hash = hashlib.sha256(b"".join(raw for _, raw in sorted(self.files)))` hexdigest
    (deterministic, sorted by object name).
  - Return `LoadedDataset(object_name=self.primary_object, source_name=self.source_name,
    fields=primary_fields, records=primary_records, source_hash=source_hash, related_records=related_records)`.
  - (schema stays None for now — `missing_children` does not need it; orphan needs schema which comes
    with a sidecar/live org later.)

## 3. API — `app/api/routes_scans.py`
Add:
```
@router.post("/api/scans/multi")
async def multi_scan(primary_object: str = Query(min_length=1, max_length=80),
                     files: list[UploadFile] = File(...),
                     container: Container = Depends(get_container)) -> dict[str, Any]:
```
- For each uploaded file: require filename ends with `.csv` (else `HTTPException(400, "Upload CSV
  files")`); `object_name = Path(file.filename).stem`; `raw = await file.read()`; if `len(raw) >
  25_000_000` → `HTTPException(413, "POC upload limit is 25 MB per file")`. Collect `(object_name, raw)`.
- Build `source = MultiCsvSource(primary_object=primary_object, files=collected,
  source_name=f"multi:{primary_object}+{len(collected)-1} related")`.
- `try: return await container.scan_service.run_scan(source)` `except IngestionError as e: raise
  HTTPException(e.status_code, str(e))`.
- Import `MultiCsvSource`, `Path`, `list`/`UploadFile` as needed.

## 4. Tests — `tests/test_multi_object.py` (NEW)
- `MultiCsvSource` unit: build Account CSV (20 rows Id+Name, Ids acc000..acc019) and Contact CSV
  (Id, AccountId, linking 17 distinct accounts). `load()` → dataset.object_name=="Account",
  len(records)==20, `related_records["Contact"]` has the contacts, source_hash non-empty. Running
  `build_default_registry().detect(ctx-from-this-dataset-via-service-or-context)` yields 3
  `missing_children`.
- Primary-missing error: `MultiCsvSource(primary_object="Nope", files=[("Account", b"...")])` →
  `load()` raises `IngestionError` with status_code 400 (use `asyncio.run` + `pytest.raises`).
- API happy path (TestClient): `POST /api/scans/multi?primary_object=Account` with two files
  `("files", ("Account.csv", <bytes>, "text/csv"))` and `("files", ("Contact.csv", <bytes>, "text/csv"))`
  → 200, scan status "completed"; then GET `/api/findings?scan_id=...` includes `missing_children`.
- API non-csv file → 400; API with primary_object not among files → 400.
- (Use a tmp `SCANNER_DATA_DIR` + importlib.reload(app.main) pattern like the other API tests.)

## Constraints
- `from __future__ import annotations`; type hints; docstrings. Only the files above are touched.
- Do NOT modify existing tests; existing single-object endpoints/sources unchanged.

## Done criteria
- `python -m pytest -q` green. Report files + results, and confirm a 2-file multi scan produces
  `missing_children` findings through the HTTP endpoint.
