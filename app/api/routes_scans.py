from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from app.ingestion.base import IngestionError
from app.ingestion.csv_source import CsvSource
from app.ingestion.multi_csv_source import MultiCsvSource
from app.ingestion.salesforce_bulk_source import SalesforceBulkSource
from app.ingestion.salesforce_source import SalesforceObjectSource
from app.ingestion.sqlite_source import SqliteTableSource

import os
import shutil
import tempfile
from app.salesforce import SAFE_API_NAME, SalesforceClient, SalesforceError, session_from_environment
from app.services.errors import NotFoundError

from .container import Container, get_container
from .schemas import BulkScanInput, OrgScanInput, SalesforceScanInput

router = APIRouter()


@router.post("/api/scans/demo")
async def run_demo_scan(container: Container = Depends(get_container)) -> dict[str, Any]:
    if not container.demo_file.exists():
        raise HTTPException(500, "Demo data is missing")
    source = CsvSource(
        raw_bytes=container.demo_file.read_bytes(),
        object_name="Opportunity",
        source_name=container.demo_file.name,
    )
    return await container.scan_service.run_scan(source)


@router.post("/api/scans/upload")
async def upload_scan(
    object_name: str = Query(min_length=1, max_length=80),
    file: UploadFile = File(...),
    container: Container = Depends(get_container),
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
        return await container.scan_service.run_scan(source)
    except IngestionError as e:
        raise HTTPException(e.status_code, str(e)) from e


@router.post("/api/scans/profile")
async def profile_scan(
    object_name: str = Query(min_length=1, max_length=80),
    file: UploadFile = File(...),
    container: Container = Depends(get_container),
) -> dict[str, Any]:
    """Bounded-memory PROFILE-ONLY scan (no detection): streams the CSV -> Parquet -> DuckDB profile."""
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(400, "Upload a CSV file")
    raw = await file.read()
    if len(raw) > 25_000_000:
        raise HTTPException(413, "POC upload limit is 25 MB")
    source = CsvSource(
        raw_bytes=raw, object_name=object_name, source_name=file.filename or "upload.csv"
    )
    try:
        return await container.scan_service.profile_source(source)
    except IngestionError as e:
        raise HTTPException(e.status_code, str(e)) from e


@router.post("/api/scans/multi")
async def multi_scan(
    primary_object: str = Query(min_length=1, max_length=80),
    files: list[UploadFile] = File(...),
    container: Container = Depends(get_container),
) -> dict[str, Any]:
    """Run a scan over a primary CSV plus related object CSV files."""
    collected: list[tuple[str, bytes]] = []
    for file in files:
        if not (file.filename or "").lower().endswith(".csv"):
            raise HTTPException(400, "Upload CSV files")
        raw = await file.read()
        if len(raw) > 25_000_000:
            raise HTTPException(413, "POC upload limit is 25 MB per file")
        object_name = Path(file.filename or "").stem
        collected.append((object_name, raw))

    source = MultiCsvSource(
        primary_object=primary_object,
        files=collected,
        source_name=f"multi:{primary_object}+{len(collected)-1} related",
    )
    try:
        return await container.scan_service.run_scan(source)
    except IngestionError as e:
        raise HTTPException(e.status_code, str(e)) from e


@router.post("/api/scans/database")
async def database_scan(
    table_name: str = Query(min_length=1, max_length=80),
    file: UploadFile = File(...),
    container: Container = Depends(get_container),
) -> dict[str, Any]:
    if not (file.filename or "").lower().endswith((".db", ".sqlite", ".sqlite3")):
        raise HTTPException(400, "Upload a SQLite database file")
    raw = await file.read()
    if len(raw) > 25_000_000:
        raise HTTPException(413, "POC upload limit is 25 MB")
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, file.filename or "upload.db")
    try:
        with open(db_path, "wb") as f:
            f.write(raw)
        source = SqliteTableSource(db_path=db_path, table_name=table_name)
        return await container.scan_service.run_scan(source)
    except IngestionError as e:
        raise HTTPException(e.status_code, str(e)) from e
    finally:
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass


@router.post("/api/scans/database/stream")
async def database_stream_scan(
    table_name: str = Query(min_length=1, max_length=80),
    file: UploadFile = File(...),
    container: Container = Depends(get_container),
) -> dict[str, Any]:
    """Streaming (bounded-memory) detecting scan over SQLite table. Same params as /database."""
    if not (file.filename or "").lower().endswith((".db", ".sqlite", ".sqlite3")):
        raise HTTPException(400, "Upload a SQLite database file")
    raw = await file.read()
    if len(raw) > 25_000_000:
        raise HTTPException(413, "POC upload limit is 25 MB")
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, file.filename or "upload.db")
    try:
        with open(db_path, "wb") as f:
            f.write(raw)
        source = SqliteTableSource(db_path=db_path, table_name=table_name)
        return await container.scan_service.stream_scan(source)
    except IngestionError as e:
        raise HTTPException(e.status_code, str(e)) from e
    finally:
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass


@router.post("/api/scans/salesforce")
async def scan_salesforce(
    input: SalesforceScanInput, container: Container = Depends(get_container)
) -> dict[str, Any]:
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
        return await container.scan_service.run_scan(source)
    except IngestionError as e:
        raise HTTPException(e.status_code, str(e)) from e
    except SalesforceError as exc:
        raise HTTPException(502 if exc.status_code else 409, str(exc)) from exc


@router.post("/api/scans/salesforce/incremental")
async def scan_salesforce_incremental(
    input: SalesforceScanInput, container: Container = Depends(get_container)
) -> dict[str, Any]:
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
        return await container.sync_service.incremental_scan(source)
    except IngestionError as e:
        raise HTTPException(e.status_code, str(e)) from e
    except SalesforceError as exc:
        raise HTTPException(502 if exc.status_code else 409, str(exc)) from exc


@router.post("/api/scans/salesforce/reconcile")
async def scan_salesforce_reconcile(
    input: SalesforceScanInput, container: Container = Depends(get_container)
) -> dict[str, Any]:
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
        return await container.sync_service.reconcile(source)
    except IngestionError as e:
        raise HTTPException(e.status_code, str(e)) from e
    except SalesforceError as exc:
        raise HTTPException(502 if exc.status_code else 409, str(exc)) from exc


@router.post("/api/scans/salesforce/bulk")
async def scan_salesforce_bulk(
    input: BulkScanInput, container: Container = Depends(get_container)
) -> dict[str, Any]:
    """Bulk API 2.0 backed streaming scan (tier-0 only). Same session/env + error mapping as /salesforce."""
    if not SAFE_API_NAME.fullmatch(input.object_name):
        raise HTTPException(400, "Invalid Salesforce object API name")
    try:
        session = await session_from_environment()
        client = SalesforceClient(session)
        source = SalesforceBulkSource(
            client=client,
            session=session,
            object_name=input.object_name,
            max_records=input.max_records,
        )
        return await container.scan_service.stream_scan(source)
    except IngestionError as e:
        raise HTTPException(e.status_code, str(e)) from e
    except SalesforceError as exc:
        raise HTTPException(502 if exc.status_code else 409, str(exc)) from exc


@router.post("/api/scans/salesforce/bulk/incremental")
async def scan_salesforce_bulk_incremental(
    input: BulkScanInput, container: Container = Depends(get_container)
) -> dict[str, Any]:
    """Incremental Bulk API 2.0 scan: streams only changed records via a watermark, ledgers ids per batch."""
    if not SAFE_API_NAME.fullmatch(input.object_name):
        raise HTTPException(400, "Invalid Salesforce object API name")
    try:
        session = await session_from_environment()
        client = SalesforceClient(session)
        source = SalesforceBulkSource(
            client=client,
            session=session,
            object_name=input.object_name,
            max_records=input.max_records,
        )
        return await container.sync_service.incremental_scan(source)
    except IngestionError as e:
        raise HTTPException(e.status_code, str(e)) from e
    except SalesforceError as exc:
        raise HTTPException(502 if exc.status_code else 409, str(exc)) from exc


@router.post("/api/scans/salesforce/org")
async def scan_salesforce_org(
    input: OrgScanInput, container: Container = Depends(get_container)
) -> dict[str, Any]:
    """Org-wide orchestrated scan: discovers every object, sizes + routes it, respects an API budget."""
    try:
        session = await session_from_environment()
        client = SalesforceClient(session)
        return await container.orchestrator_service.scan_org(
            client=client,
            session=session,
            include=input.include,
            exclude=input.exclude,
            max_objects=input.max_objects,
            stream_threshold=input.stream_threshold,
            api_budget=input.api_budget,
        )
    except SalesforceError as exc:
        raise HTTPException(502 if exc.status_code else 409, str(exc)) from exc


@router.get("/api/scans")
def list_scans(container: Container = Depends(get_container)) -> list[dict[str, Any]]:
    return container.scan_service.list_scans()


@router.get("/api/scans/{scan_id}")
def get_scan(scan_id: str, container: Container = Depends(get_container)) -> dict[str, Any]:
    try:
        return container.scan_service.get_scan_detail(scan_id)
    except NotFoundError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/api/scans/{scan_id}/quality")
def get_scan_quality(scan_id: str, container: Container = Depends(get_container)) -> dict[str, Any]:
    """Return data quality metrics for a scan."""
    try:
        return container.quality_service.for_scan(scan_id)
    except NotFoundError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/api/scans/{scan_id}/drift")
def get_scan_drift(scan_id: str, container: Container = Depends(get_container)) -> dict[str, Any]:
    try:
        return container.drift_service.compare(scan_id)
    except NotFoundError as e:
        raise HTTPException(404, str(e)) from e
