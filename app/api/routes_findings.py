from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.services.errors import NotFoundError

from .container import Container, get_container
from .schemas import FeedbackInput

router = APIRouter()


@router.get("/api/findings")
def list_findings(
    scan_id: str | None = None,
    status: str | None = None,
    container: Container = Depends(get_container),
) -> list[dict[str, Any]]:
    return container.finding_service.list_findings(scan_id, status)


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
