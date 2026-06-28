from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from .container import Container, get_container

router = APIRouter()


@router.get("/api/audit")
def list_audit(
    limit: int = Query(default=100, ge=1, le=1000),
    container: Container = Depends(get_container),
) -> list[dict[str, Any]]:
    return container.audit_service.list(limit)
