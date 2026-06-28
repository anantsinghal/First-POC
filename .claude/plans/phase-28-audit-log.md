# Phase 28 — Audit logging (append-only, non-sensitive)

Record an append-only audit trail of significant actions (scan completed, feedback submitted) for
enterprise traceability. NEVER store record values, field values, or secrets — only counts, grades,
labels, ids. ADDITIVE.

## 1. Persistence — `app/persistence/database.py`
`setup()` add (IF NOT EXISTS):
`audit_log (id TEXT PRIMARY KEY, ts TEXT NOT NULL, event TEXT NOT NULL, actor TEXT NOT NULL,
scan_id TEXT, object_name TEXT, detail_json TEXT NOT NULL)`.
Add `"detail_json"` to `serialise_row`'s `_json` key list (→ `detail`).

## 2. Repository — `app/persistence/repositories.py` — `class AuditRepository`
- `append(self, conn, *, event: str, actor: str, scan_id: str | None = None, object_name: str | None =
  None, detail: dict[str, Any]) -> None` — INSERT `id=str(uuid4())`, `ts=now()`, event, actor, scan_id,
  object_name, `detail_json=json.dumps(detail)`.
- `list(self, conn, limit: int = 100) -> list[dict]` — `SELECT * FROM audit_log ORDER BY ts DESC LIMIT
  ?`, serialised (detail parsed).

## 3. Services
- `ScanService.__init__`: add `audit: AuditRepository` (store). In `run_scan`, inside the unit of work
  AFTER `complete`/`save_schema`, call `self.audit.append(conn, event="scan_completed", actor="system",
  scan_id=scan_id, object_name=dataset.object_name, detail={"finding_count": len(candidates),
  "coverage_grade": coverage.grade.value, "row_count": len(dataset.records)})`.
- `FindingService.__init__`: add `audit: AuditRepository` (store). In `add_feedback`, inside the
  connect() AFTER `set_status`, call `self.audit.append(conn, event="feedback_added", actor=reviewer,
  detail={"finding_id": finding_id, "label": label, "new_status": state})`.
- NEW `app/services/audit_service.py` — `class AuditService` with `__init__(self, *, database: Database,
  audit: AuditRepository)` and `def list(self, limit: int = 100) -> list[dict]` (open connect → audit.list).

## 4. Composition — `app/api/container.py`
Build `audit = AuditRepository()`; pass it into `ScanService` and `FindingService`; build
`audit_service = AuditService(database=database, audit=audit)`; add `audit_service` to the `Container`
dataclass.

## 5. API — `app/api/routes_audit.py` (NEW) + register in `app/main.py`
- `router = APIRouter()`; `GET "/api/audit"` (optional `limit: int = Query(default=100, ge=1, le=1000)`)
  -> `container.audit_service.list(limit)`. Use `container: Container = Depends(get_container)`.
- In `main.create_app()` `app.include_router(audit_router)`.

## 6. Tests — `tests/test_audit.py` (NEW)
- AuditRepository unit (tmp Database): append two events, `list` returns them newest-first with parsed
  `detail`.
- Via TestClient (tmp SCANNER_DATA_DIR + reload app.main): POST `/api/scans/demo`; GET `/api/audit`
  includes a `scan_completed` entry with `detail.finding_count == 10` and `detail.coverage_grade ==
  "good"`; then POST feedback on a finding; GET `/api/audit` now includes a `feedback_added` entry with
  the label. Assert NO audit `detail` value contains a token/secret (sanity: the detail keys are exactly
  the small set above).

## Constraints
- `from __future__ import annotations`; type hints; docstrings. Existing endpoints/behavior unchanged
  (constructor signature changes are internal — update container + any direct test construction if
  needed, but prefer not to touch existing tests; if a persistence test constructs services directly it
  shouldn't, since services are built in the container).
- Audit must contain NO record/field values or secrets.

## Done criteria
- `python -m pytest -q` green. Report files + results; confirm a demo scan and a feedback action each
  produce an audit entry visible at GET /api/audit, with counts/labels only.
