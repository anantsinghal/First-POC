# Phase 26 — Metadata-catalog persistence (Org Catalog)

Persist the discovered `ObjectSchema` for each scan that has one (Salesforce scans), so the org's
metadata snapshot is stored alongside the immutable scan — foundation for drift detection, audit, and
reproducibility. ADDITIVE; CSV scans (schema=None) simply store no schema.

## 1. Domain — `app/domain/schema.py`
Add `ObjectSchema.to_dict() -> dict[str, Any]` producing the SAME shape `schema_from_dict` consumes:
`{"object_name": ..., "fields": [{"name","data_type"(.value),"required","picklist_values"(sorted list
or None),"reference_to"(list),"is_name_field","is_unique","length"}, ...]}`. (Round-trip:
`schema_from_dict(s.to_dict())` must equal `s`.)

## 2. Persistence — `app/persistence/database.py`
In `setup()` add a table (IF NOT EXISTS):
`scan_schemas (scan_id TEXT PRIMARY KEY REFERENCES scan_manifests(id), object_name TEXT NOT NULL,
schema_json TEXT NOT NULL)`.
Add `"schema_json"` to the `serialise_row` `_json` key list so it deserialises to `schema`.

## 3. Persistence — `app/persistence/repositories.py` (ScanRepository)
- `save_schema(self, conn, *, scan_id, object_name, schema: dict[str, Any]) -> None` — INSERT
  (scan_id, object_name, json.dumps(schema)).
- `get_schema(self, conn, scan_id) -> dict | None` — SELECT schema_json; return parsed dict or None.

## 4. Service — `app/services/scan_service.py`
- In `run_scan`, inside the unit of work, after `scans.complete(...)`: if `dataset.schema is not None`,
  call `self.scans.save_schema(conn, scan_id=scan_id, object_name=dataset.object_name,
  schema=dataset.schema.to_dict())`.
- In `get_scan_detail`, add `result["schema"] = self.scans.get_schema(conn, scan_id)` (None when absent).

## 5. Tests — `tests/test_org_catalog.py` (NEW)
- Round-trip: build an ObjectSchema via `schema_from_dict`, assert `schema_from_dict(s.to_dict())` has
  identical fields (same names/types/required/picklist_values/reference_to).
- Persistence: with a tmp `Database`, in one connect(): insert_running + complete + save_schema(a dict);
  reopen, `get_schema(scan_id)` returns the dict.
- Service wiring: a fake `DataSource` returning a `LoadedDataset` with a schema (use schema_from_dict);
  run via `ScanService.run_scan`; assert the returned scan_detail has `result["schema"]` with the right
  object_name and fields. And a CSV-style dataset with schema=None → `result["schema"] is None`.

## Constraints
- `from __future__ import annotations`; type hints; docstrings.
- Do not change existing finding/coverage behavior or existing tests. Demo unaffected (schema None).

## Done criteria
- `python -m pytest -q` green. Report files changed + results, and confirm a schema'd scan persists and
  returns its schema in scan_detail while a CSV scan returns schema None.
