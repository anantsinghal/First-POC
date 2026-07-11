from __future__ import annotations

from typing import Any

from ..persistence.database import Database
from ..persistence.repositories import AuditRepository


class AuditService:
    """Service over the append-only audit log (list + append for middleware/ops)."""

    def __init__(self, *, database: Database, audit: AuditRepository) -> None:
        """Store injected dependencies."""
        self.database = database
        self.audit = audit

    def list(self, limit: int = 100) -> list[dict]:
        """Return up to `limit` most recent audit entries (newest first), with parsed detail."""
        with self.database.connect() as conn:
            return self.audit.list(conn, limit=limit)

    def append(
        self,
        *,
        event: str,
        actor: str,
        detail: dict[str, Any],
        scan_id: str | None = None,
        object_name: str | None = None,
    ) -> None:
        """Append one audit row. detail must never contain secrets or record payloads."""
        with self.database.connect() as conn:
            self.audit.append(
                conn,
                event=event,
                actor=actor,
                scan_id=scan_id,
                object_name=object_name,
                detail=detail,
            )
