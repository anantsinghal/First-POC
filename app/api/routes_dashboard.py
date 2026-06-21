from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from .container import Container, get_container

router = APIRouter()


@router.get("/api/dashboard")
def dashboard(container: Container = Depends(get_container)) -> dict[str, Any]:
    return container.dashboard_service.dashboard()
