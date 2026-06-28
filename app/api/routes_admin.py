from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .container import Container, get_container

router = APIRouter()


class PurgeInput(BaseModel):
    older_than_days: int = Field(ge=1, le=3650)


@router.post("/api/admin/purge")
def purge(
    body: PurgeInput,
    container: Container = Depends(get_container),
) -> dict[str, Any]:
    return container.retention_service.purge(body.older_than_days)
