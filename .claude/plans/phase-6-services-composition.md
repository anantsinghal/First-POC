# Phase 6 — Services + composition root (the integration phase)

Introduce the service layer that orchestrates ingestion -> profile -> coverage -> detect -> persist,
then rewrite `app/main.py` as a thin **composition root** (wires concrete deps) + delegating route
handlers. Every existing endpoint keeps its path/method/JSON shape. `tests/test_scanner.py` must stay
green. This phase REPLACES the baseline business logic in `main.py` (analyze/profile_records/
calculate_coverage/persist_scan/scan_detail/severity_for/etc.) with calls into the new layers.

## `app/services/__init__.py`, `app/services/errors.py`
- `errors.py`: `class NotFoundError(Exception)` — raised by services when an entity is missing; the
  API maps it to HTTP 404.

## `app/services/scan_service.py` — `class ScanService`
Constructor (dependency injection — store all):
`__init__(self, *, database: Database, profiler: Profiler, grader: CoverageGrader,
registry: DetectorRegistry, scans: ScanRepository, coverage_repo: CoverageRepository,
findings: FindingRepository, policy: FieldPolicy)`.

- `async def run_scan(self, source: DataSource) -> dict`:
  1. `self.database.setup()` (defensive/idempotent, matches baseline persist_scan).
  2. `dataset = await source.load()` (do this BEFORE any DB write so a load failure persists nothing).
  3. `scan_id = str(uuid4())`; `created = now()`.
  4. In a single `with self.database.connect() as conn:` unit of work:
     - `self.scans.insert_running(conn, scan_id=scan_id, source_name=dataset.source_name,
       object_name=dataset.object_name, created_at=created, row_count=len(dataset.records),
       source_hash=dataset.source_hash, fields=dataset.fields)`
     - `coverage = self.grader.grade(dataset.object_name, dataset.records, dataset.fields)`
     - `self.coverage_repo.insert(conn, scan_id, coverage)`
     - `profile = self.profiler.profile(dataset.records, dataset.fields)`
     - candidates: if `coverage.grade == CoverageGrade.BLOCKED`: `candidates = []`; else
       `ctx = DetectionContext(dataset.object_name, dataset.records, dataset.fields, self.policy,
       profile)` then `candidates = self.registry.detect(ctx)`.
     - `for c in candidates: self.findings.upsert(conn, scan_id=scan_id, candidate=c,
       source_hash=dataset.source_hash, scan_created_at=created)`
     - `self.scans.complete(conn, scan_id=scan_id, completed_at=now(), profile=profile.to_dict(),
       notes=f"{len(candidates)} candidate occurrences generated.")`
  5. `return self.get_scan_detail(scan_id)`.
- `def get_scan_detail(self, scan_id: str) -> dict`: in a `connect()`:
  `manifest = self.scans.get(conn, scan_id)`; if None raise `NotFoundError("Scan not found")`;
  `cov = self.coverage_repo.get(conn, scan_id)`; `count = self.findings.count_for_scan(conn, scan_id)`;
  set `manifest["coverage"] = cov` (or None); `manifest["finding_count"] = count`; return manifest.
  (Same shape as baseline `scan_detail`.)
- `def list_scans(self) -> list[dict]`: `connect()` -> `self.scans.list(conn)`.

## `app/services/finding_service.py` — `class FindingService`
`__init__(self, *, database: Database, findings: FindingRepository, feedback: FeedbackRepository)`.
- `def list_findings(self, scan_id: str | None = None, status: str | None = None) -> list[dict]`:
  `connect()`; `rows = self.findings.list(conn, scan_id=scan_id, status=status)`; for each, attach
  `row["feedback"] = self.feedback.list_for_finding(conn, row["id"])`; return rows. (Matches baseline.)
- `def add_feedback(self, finding_id: str, label: str, note: str, reviewer: str) -> dict`:
  `state = {"real_issue":"acknowledged","false_positive":"suppressed_until",
  "valid_exception":"suppressed_until","needs_review":"open"}[label]`; in `connect()`: if not
  `self.findings.exists(conn, finding_id)` raise `NotFoundError("Finding not found")`;
  `self.feedback.insert(conn, finding_id=finding_id, label=label, note=note, reviewer=reviewer)`;
  `self.findings.set_status(conn, finding_id, state)`; return
  `{"finding_id": finding_id, "status": state, "label": label}`.

## `app/services/dashboard_service.py` — `class DashboardService`
`__init__(self, *, database: Database, scans: ScanRepository, findings: FindingRepository,
scan_service: ScanService)`.
- `def dashboard(self) -> dict`: in `connect()`: `latest = self.scans.latest_id(conn)`;
  `total = self.scans.count(conn)`; `open_findings = self.findings.count_open(conn)`. Then
  `latest_scan = self.scan_service.get_scan_detail(latest) if latest else None`. Return
  `{"total_scans": total, "open_findings": open_findings, "latest_scan": latest_scan}`. (Matches baseline.)

## Rewrite `app/main.py` — composition root + thin handlers
Keep module-level wiring (so tests that reload the module with a patched `SCANNER_DATA_DIR` re-wire):
- Config at module import: `ROOT = Path(__file__).resolve().parents[1]`,
  `DATA_DIR = Path(os.environ.get("SCANNER_DATA_DIR", ROOT / "data"))`, `DB_PATH = DATA_DIR /
  "scanner.db"`, `DEMO_FILE = ROOT / "data" / "demo_opportunities.csv"`.
- Build singletons: `database = Database(DB_PATH)`, `profiler = Profiler()`,
  `grader = CoverageGrader(DEFAULT_POLICY)`, `registry = build_default_registry()`, the four
  repositories, then `scan_service`, `finding_service`, `dashboard_service` with the deps above.
- Use a **lifespan** handler (NOT `@app.on_event`) that calls `database.setup()` on startup:
  `@asynccontextmanager async def lifespan(app): database.setup(); yield`. Pass `lifespan=lifespan`
  to `FastAPI(...)`. (This removes the deprecation warning.)
- Mount static dir + keep `index()` (`/`) and `tutorial()` (`/tutorial`) FileResponses exactly.
- Keep pydantic models `FeedbackInput` and `SalesforceScanInput` in main.py for now (Phase 7 moves them).
- Route handlers (identical paths/methods/shapes), delegating to services and mapping errors:
  - `GET /api/health` -> `{"status":"ok","mode":"local-poc"}`.
  - `POST /api/scans/demo` -> if `not DEMO_FILE.exists()` raise `HTTPException(500,"Demo data is
    missing")`; else `source = CsvSource(raw_bytes=DEMO_FILE.read_bytes(), object_name="Opportunity",
    source_name=DEMO_FILE.name)`; `return await scan_service.run_scan(source)`.
  - `POST /api/scans/upload` (Query `object_name`, File `file`): validate `.csv` suffix (else
    `HTTPException(400,"Upload a CSV file")`), `raw = await file.read()`, size > 25_000_000 ->
    `HTTPException(413,"POC upload limit is 25 MB")`; `source = CsvSource(raw_bytes=raw,
    object_name=object_name, source_name=file.filename or "upload.csv")`; wrap `run_scan` in
    `try/except IngestionError as e: raise HTTPException(e.status_code, str(e))`.
  - `POST /api/scans/salesforce` (`SalesforceScanInput`): if not
    `SAFE_API_NAME.fullmatch(input.object_name)` -> `HTTPException(400,"Invalid Salesforce object API
    name")`; then `try: session = await session_from_environment(); client =
    SalesforceClient(session); source = SalesforceObjectSource(client=client, session=session,
    object_name=input.object_name, max_records=input.max_records); return await
    scan_service.run_scan(source)` `except IngestionError as e: raise HTTPException(e.status_code,
    str(e))` `except SalesforceError as e: raise HTTPException(502 if e.status_code else 409, str(e))`.
  - `POST /api/salesforce/verify` -> `try: return await verify_direct_api_access() except
    SalesforceError as e: raise HTTPException(409 if e.status_code is None else 502, str(e))`.
  - `GET /api/scans` -> `scan_service.list_scans()`.
  - `GET /api/scans/{scan_id}` -> `try: return scan_service.get_scan_detail(scan_id) except
    NotFoundError as e: raise HTTPException(404, str(e))`.
  - `GET /api/findings` (Query scan_id, status) -> `finding_service.list_findings(scan_id, status)`.
  - `POST /api/findings/{finding_id}/feedback` (`FeedbackInput`) -> `try: return
    finding_service.add_feedback(finding_id, fb.label, fb.note, fb.reviewer) except NotFoundError as
    e: raise HTTPException(404, str(e))`.
  - `GET /api/dashboard` -> `dashboard_service.dashboard()`.

## Constraints
- Do not change `tests/test_scanner.py`. After this phase the baseline helper functions in main.py
  are gone — that is intended.
- services import lower layers + domain only (NO fastapi). Only `main.py` imports fastapi.
- Type hints + docstrings. `from __future__ import annotations`.

## Done criteria
- `python -m pytest -q` fully green (all prior tests including test_scanner.py).
- `python -c "import app.main"` imports clean with no deprecation warning about on_event.
- Report the new files + the rewritten main.py + test results.
