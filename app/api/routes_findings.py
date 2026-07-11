from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.services.errors import NotFoundError

from .container import Container, get_container
from .schemas import FeedbackInput

router = APIRouter()


@router.get("/api/findings")
def list_findings(
    response: Response,
    scan_id: str | None = None,
    status: str | None = None,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    container: Container = Depends(get_container),
) -> list[dict[str, Any]]:
    """Paginated findings. Response STAYS a bare list (test_scanner.py contract); the total row
    count (ignoring limit/offset) is exposed via the X-Total-Count header for the dashboard."""
    response.headers["X-Total-Count"] = str(container.finding_service.count_findings(scan_id, status))
    return container.finding_service.list_findings(scan_id, status, limit=limit, offset=offset)


@router.post("/api/findings/{finding_id}/feedback")
def add_feedback(
    finding_id: str,
    feedback: FeedbackInput,
    container: Container = Depends(get_container),
) -> dict[str, Any]:
    try:
        return container.finding_service.add_feedback(
            finding_id, feedback.label, feedback.note, feedback.reviewer
        )
    except NotFoundError as e:
        raise HTTPException(404, str(e)) from e
