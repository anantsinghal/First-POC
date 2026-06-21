from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from app.ingestion.base import IngestionError
from app.ingestion.csv_source import CsvSource
from app.ingestion.salesforce_source import SalesforceObjectSource
from app.salesforce import SAFE_API_NAME, SalesforceClient, SalesforceError, session_from_environment
from app.services.errors import NotFoundError

from .container import Container, get_container
from .schemas import SalesforceScanInput

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


@router.get("/api/scans")
def list_scans(container: Container = Depends(get_container)) -> list[dict[str, Any]]:
    return container.scan_service.list_scans()


@router.get("/api/scans/{scan_id}")
def get_scan(scan_id: str, container: Container = Depends(get_container)) -> dict[str, Any]:
    try:
        return container.scan_service.get_scan_detail(scan_id)
    except NotFoundError as e:
        raise HTTPException(404, str(e)) from e
