from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from .container import Container, get_container

router = APIRouter()


@router.get("/api/metrics")
def metrics(container: Container = Depends(get_container)) -> dict[str, Any]:
    """Review/trust metrics: counts by status/severity/type + acceptance & false-positive rates."""
    return container.metrics_service.overview()
