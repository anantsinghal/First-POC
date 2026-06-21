from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.coverage.grader import CoverageGrader
from app.detection.registry import build_default_registry
from app.ingestion.base import IngestionError
from app.ingestion.csv_source import CsvSource
from app.ingestion.salesforce_source import SalesforceObjectSource
from app.persistence.database import Database
from app.persistence.repositories import (
    CoverageRepository,
    FeedbackRepository,
    FindingRepository,
    ScanRepository,
)
from app.policy.field_policy import DEFAULT_POLICY
from app.profiling.profiler import Profiler
from app.salesforce import (
    SAFE_API_NAME,
    SalesforceClient,
    SalesforceError,
    session_from_environment,
    verify_direct_api_access,
)
from app.services.dashboard_service import DashboardService
from app.services.errors import NotFoundError
from app.services.finding_service import FindingService
from app.services.scan_service import ScanService


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("SCANNER_DATA_DIR", ROOT / "data"))
DB_PATH = DATA_DIR / "scanner.db"
DEMO_FILE = ROOT / "data" / "demo_opportunities.csv"


# Module-level singletons (composition root). Reloading main (as tests do) re-wires with current env.
database = Database(DB_PATH)
profiler = Profiler()
grader = CoverageGrader(DEFAULT_POLICY)
registry = build_default_registry()
scans = ScanRepository()
coverage_repo = CoverageRepository()
findings = FindingRepository()
feedback = FeedbackRepository()

scan_service = ScanService(
    database=database,
    profiler=profiler,
    grader=grader,
    registry=registry,
    scans=scans,
    coverage_repo=coverage_repo,
    findings=findings,
    policy=DEFAULT_POLICY,
)
finding_service = FindingService(
    database=database,
    findings=findings,
    feedback=feedback,
)
dashboard_service = DashboardService(
    database=database,
    scans=scans,
    findings=findings,
    scan_service=scan_service,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan handler (replaces deprecated @app.on_event startup)."""
    database.setup()
    yield


app = FastAPI(title="Salesforce Data Quality Scanner POC", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "app" / "static"), name="static")


class FeedbackInput(BaseModel):
    label: str = Field(pattern="^(real_issue|false_positive|valid_exception|needs_review)$")
    note: str = Field(default="", max_length=1000)
    reviewer: str = Field(default="local-reviewer", min_length=1, max_length=120)


class SalesforceScanInput(BaseModel):
    object_name: str = Field(default="Opportunity", min_length=1, max_length=80)
    max_records: int = Field(default=500, ge=1, le=2000)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(ROOT / "app" / "static" / "index.html")


@app.get("/tutorial", include_in_schema=False)
def tutorial() -> FileResponse:
    return FileResponse(ROOT / "app" / "static" / "ml-tutorial.html")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "mode": "local-poc"}


@app.post("/api/scans/demo")
async def run_demo_scan() -> dict[str, Any]:
    if not DEMO_FILE.exists():
        raise HTTPException(500, "Demo data is missing")
    source = CsvSource(
        raw_bytes=DEMO_FILE.read_bytes(), object_name="Opportunity", source_name=DEMO_FILE.name
    )
    return await scan_service.run_scan(source)


@app.post("/api/salesforce/verify")
async def verify_salesforce() -> dict[str, Any]:
    """Prove direct access to REST/Describe, Tooling, and Metadata SOAP APIs."""
    try:
        return await verify_direct_api_access()
    except SalesforceError as exc:
        raise HTTPException(409 if exc.status_code is None else 502, str(exc)) from exc


@app.post("/api/scans/salesforce")
async def scan_salesforce(input: SalesforceScanInput) -> dict[str, Any]:
    """Extract a bounded object slice via direct REST API, then run the same scanner."""
    if not SAFE_API_NAME.fullmatch(input.object_name):
        raise HTTPException(400, "Invalid Salesforce object API name")
    try:
        session = await session_from_environment()
        client = SalesforceClient(session)
        source = SalesforceObjectSource(
            client=client,
            session=session,
            object_name=input.object_name,
            max_records=input.max_records,
        )
        return await scan_service.run_scan(source)
    except IngestionError as e:
        raise HTTPException(e.status_code, str(e)) from e
    except SalesforceError as exc:
        raise HTTPException(502 if exc.status_code else 409, str(exc)) from exc


@app.post("/api/scans/upload")
async def upload_scan(
    object_name: str = Query(min_length=1, max_length=80), file: UploadFile = File(...)
) -> dict[str, Any]:
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(400, "Upload a CSV file")
    raw = await file.read()
    if len(raw) > 25_000_000:
        raise HTTPException(413, "POC upload limit is 25 MB")
    source = CsvSource(
        raw_bytes=raw, object_name=object_name, source_name=file.filename or "upload.csv"
    )
    try:
        return await scan_service.run_scan(source)
    except IngestionError as e:
        raise HTTPException(e.status_code, str(e)) from e


@app.get("/api/scans")
def list_scans() -> list[dict[str, Any]]:
    return scan_service.list_scans()


@app.get("/api/scans/{scan_id}")
def get_scan(scan_id: str) -> dict[str, Any]:
    try:
        return scan_service.get_scan_detail(scan_id)
    except NotFoundError as e:
        raise HTTPException(404, str(e)) from e


@app.get("/api/findings")
def list_findings(scan_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
    return finding_service.list_findings(scan_id, status)


@app.post("/api/findings/{finding_id}/feedback")
def add_feedback(finding_id: str, feedback: FeedbackInput) -> dict[str, Any]:
    try:
        return finding_service.add_feedback(
            finding_id, feedback.label, feedback.note, feedback.reviewer
        )
    except NotFoundError as e:
        raise HTTPException(404, str(e)) from e


@app.get("/api/dashboard")
def dashboard() -> dict[str, Any]:
    return dashboard_service.dashboard()
