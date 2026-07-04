from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from .container import Container, get_container

router = APIRouter()


@router.post("/api/learning/train")
def train_model(container: Container = Depends(get_container)) -> dict[str, Any]:
    """Train the supervised ranking model from reviewer feedback (cold-start guarded; always 200)."""
    return container.learning_service.train()


@router.get("/api/learning/status")
def learning_status(container: Container = Depends(get_container)) -> dict[str, Any]:
    """Latest model summary + current label counts + whether cold-start thresholds are met."""
    return container.learning_service.status()
