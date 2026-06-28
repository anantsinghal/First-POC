from __future__ import annotations

from ..persistence.database import Database
from ..persistence.repositories import AuditRepository


class AuditService:
    """Read-only service over the append-only audit log."""

    def __init__(self, *, database: Database, audit: AuditRepository) -> None:
        """Store injected dependencies."""
        self.database = database
        self.audit = audit

    def list(self, limit: int = 100) -> list[dict]:
        """Return up to `limit` most recent audit entries (newest first), with parsed detail."""
        with self.database.connect() as conn:
            return self.audit.list(conn, limit=limit)
