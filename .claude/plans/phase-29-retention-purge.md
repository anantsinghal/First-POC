# Phase 29 — Retention / purge controls

Let an operator purge scans older than a configurable age — deleting the per-scan data (manifest,
coverage, schema snapshot, findings, and those findings' feedback) while the append-only **audit log
is preserved** (durable record that the scan happened). Honors the "keep the valuable, purge the rest"
policy. ADDITIVE.

## 1. Repository — `app/persistence/repositories.py` (ScanRepository)
- `ids_older_than(self, conn, cutoff_iso: str) -> list[str]` —
  `SELECT id FROM scan_manifests WHERE created_at < ?`, return list of ids.
- `purge_scan(self, conn, scan_id: str) -> None` — run, in order:
  `DELETE FROM feedback WHERE finding_id IN (SELECT id FROM findings WHERE scan_id=?)`;
  `DELETE FROM findings WHERE scan_id=?`; `DELETE FROM coverage WHERE scan_id=?`;
  `DELETE FROM scan_schemas WHERE scan_id=?`; `DELETE FROM scan_manifests WHERE id=?`.
  (audit_log is intentionally NOT touched.)

## 2. Service — `app/services/retention_service.py` — `class RetentionService`
- `__init__(self, *, database: Database, scans: ScanRepository, audit: AuditRepository)`.
- `def purge(self, older_than_days: int) -> dict[str, Any]`:
  - `cutoff = (datetime.now(UTC) - timedelta(days=older_than_days)).isoformat()`.
  - In one `connect()`: `ids = self.scans.ids_older_than(conn, cutoff)`; for each id
    `self.scans.purge_scan(conn, id)`; then `self.audit.append(conn, event="scans_purged",
    actor="system", detail={"purged_scans": len(ids), "older_than_days": older_than_days})`.
  - Return `{"purged_scans": len(ids), "older_than_days": older_than_days, "cutoff": cutoff}`.
  - (created_at is UTC ISO via `now()`, so lexicographic `<` on the ISO string is a correct time
    comparison.)

## 3. Composition — `app/api/container.py`
Build `retention_service = RetentionService(database=database, scans=scans, audit=audit)`; add
`retention_service` to the `Container` dataclass.

## 4. API — `app/api/routes_admin.py` (NEW) + register in `app/main.py`
- Pydantic `class PurgeInput(BaseModel)`: `older_than_days: int = Field(ge=1, le=3650)`.
- `router = APIRouter()`; `POST "/api/admin/purge"` (body `PurgeInput`,
  `container: Container = Depends(get_container)`) -> `container.retention_service.purge(
  body.older_than_days)`.
- `main.create_app()`: `app.include_router(admin_router)`.

## 5. Tests — `tests/test_retention.py` (NEW)
- Repo/service (tmp Database): create a RECENT scan (insert_running with `created_at=now()` + complete +
  a coverage row + a finding + a feedback + a schema) and an OLD scan (insert_running with
  `created_at="2020-01-01T00:00:00+00:00"` + the same dependents). Run
  `RetentionService(...).purge(older_than_days=30)`. Assert: result `purged_scans == 1`; the OLD scan's
  manifest/coverage/findings/feedback/schema are all gone; the RECENT scan and its data remain; and an
  `scans_purged` audit entry exists.
- Validation/no-op: `purge(older_than_days=3650)` on only-recent data → `purged_scans == 0`, recent data
  intact.
- API: TestClient POST `/api/admin/purge` with `{"older_than_days": 30}` returns 200 with the counts;
  `{"older_than_days": 0}` → 422 (validation).

## Constraints
- `from __future__ import annotations`; type hints; docstrings. Existing behavior/tests unchanged.
- Purge must NOT delete `audit_log` rows.

## Done criteria
- `python -m pytest -q` green. Report files + results; confirm old scans purge (manifest+coverage+
  findings+feedback+schema) while recent scans and the audit log remain.
