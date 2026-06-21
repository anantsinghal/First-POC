from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from .container import Container, get_container

router = APIRouter()


@router.get("/", include_in_schema=False)
def index(container: Container = Depends(get_container)) -> FileResponse:
    return FileResponse(container.root / "app" / "static" / "index.html")


@router.get("/tutorial", include_in_schema=False)
def tutorial(container: Container = Depends(get_container)) -> FileResponse:
    return FileResponse(container.root / "app" / "static" / "ml-tutorial.html")


@router.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "mode": "local-poc"}
