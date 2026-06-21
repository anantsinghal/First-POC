# Phase 7 — API layer (routers + DTOs)

Extract the route handlers from `app/main.py` into `app/api/` routers and move the pydantic DTOs to
`app/api/schemas.py`. `main.py` becomes a pure app factory. Wiring is exposed to routers via a DI
container stored on `app.state`, so the test pattern (`importlib.reload(app.main)` then
`TestClient(main.app)`) keeps working. **All endpoint paths/methods/JSON shapes stay identical** and
`tests/test_scanner.py` must stay green (unmodified).

## `app/api/__init__.py`
Empty package marker.

## `app/api/schemas.py`
Move the two pydantic models VERBATIM from `main.py`:
- `class FeedbackInput(BaseModel)` (label pattern, note, reviewer fields exactly as now).
- `class SalesforceScanInput(BaseModel)` (object_name, max_records exactly as now).

## `app/api/container.py`
- `@dataclass class Container`: holds `root: Path`, `demo_file: Path`, `database: Database`,
  `scan_service: ScanService`, `finding_service: FindingService`, `dashboard_service: DashboardService`.
- `def build_container(*, data_dir: Path, root: Path) -> Container`: build `Database(data_dir /
  "scanner.db")`, `Profiler()`, `CoverageGrader(DEFAULT_POLICY)`, `build_default_registry()`, the four
  repositories, the three services (same wiring as current main.py), and return the populated
  Container with `demo_file = root / "data" / "demo_opportunities.csv"`.
- `def get_container(request: Request) -> Container`: return `request.app.state.container`. (Used as a
  FastAPI dependency so handlers receive the container.)

## Routers (each a module-level `router = APIRouter()`; handlers take `container: Container =
Depends(get_container)`; keep identical paths/methods/response shapes and the SAME error mapping)

`app/api/routes_system.py`:
- `GET "/"` (include_in_schema=False) -> `FileResponse(container.root / "app" / "static" /
  "index.html")`.
- `GET "/tutorial"` (include_in_schema=False) -> `FileResponse(container.root / "app" / "static" /
  "ml-tutorial.html")`.
- `GET "/api/health"` -> `{"status": "ok", "mode": "local-poc"}`.

`app/api/routes_scans.py`:
- `POST "/api/scans/demo"` -> demo file existence 500, `CsvSource`, `await
  container.scan_service.run_scan(source)`.
- `POST "/api/scans/upload"` (Query object_name, File file) -> csv suffix 400, 25MB 413, `CsvSource`,
  run_scan wrapped in `except IngestionError -> HTTPException(e.status_code, str(e))`.
- `POST "/api/scans/salesforce"` (SalesforceScanInput) -> SAFE_API_NAME 400, build session/client/
  `SalesforceObjectSource`, run_scan; `except IngestionError -> HTTPException(e.status_code)`;
  `except SalesforceError -> HTTPException(502 if e.status_code else 409)`.
- `GET "/api/scans"` -> `container.scan_service.list_scans()`.
- `GET "/api/scans/{scan_id}"` -> `try container.scan_service.get_scan_detail(scan_id) except
  NotFoundError -> HTTPException(404)`.

`app/api/routes_findings.py`:
- `GET "/api/findings"` (Query scan_id, status) -> `container.finding_service.list_findings(...)`.
- `POST "/api/findings/{finding_id}/feedback"` (FeedbackInput) -> `try
  container.finding_service.add_feedback(finding_id, fb.label, fb.note, fb.reviewer) except
  NotFoundError -> HTTPException(404)`.

`app/api/routes_salesforce.py`:
- `POST "/api/salesforce/verify"` -> `try return await verify_direct_api_access() except
  SalesforceError -> HTTPException(409 if e.status_code is None else 502)`.

`app/api/routes_dashboard.py`:
- `GET "/api/dashboard"` -> `container.dashboard_service.dashboard()`.

## Rewrite `app/main.py` (pure factory)
- Keep `ROOT` and `DATA_DIR` (from `SCANNER_DATA_DIR`) at module level.
- `def create_app() -> FastAPI`:
  - `container = build_container(data_dir=DATA_DIR, root=ROOT)`
  - `@asynccontextmanager async def lifespan(app): container.database.setup(); yield`
  - `app = FastAPI(title="Salesforce Data Quality Scanner POC", version="0.1.0", lifespan=lifespan)`
  - `app.state.container = container`
  - `app.mount("/static", StaticFiles(directory=ROOT / "app" / "static"), name="static")`
  - `app.include_router(...)` for all five routers.
  - return app
- Module level: `app = create_app()` (so `main.app` exists for tests and uvicorn `app.main:app`).

## Constraints
- Behavior identical; do not modify `tests/test_scanner.py` or any other existing test.
- Only the api layer + main.py import fastapi. Services/domain remain framework-free.
- Type hints + docstrings; `from __future__ import annotations`.

## Done criteria
- `python -m pytest -q` green.
- `uvicorn`-style import `python -c "import app.main; assert app.main.app"` works.
- Report files created/modified and test output.
