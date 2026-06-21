from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.salesforce import SalesforceError, verify_direct_api_access

from .container import Container, get_container

router = APIRouter()


@router.post("/api/salesforce/verify")
async def verify_salesforce(container: Container = Depends(get_container)) -> dict[str, Any]:
    """Prove direct access to REST/Describe, Tooling, and Metadata SOAP APIs."""
    try:
        return await verify_direct_api_access()
    except SalesforceError as exc:
        raise HTTPException(409 if exc.status_code is None else 502, str(exc)) from exc
