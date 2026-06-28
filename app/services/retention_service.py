from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ..persistence.database import Database
from ..persistence.repositories import AuditRepository, ScanRepository


class RetentionService:
    """Manages retention/purge of old per-scan data while preserving the audit log."""

    def __init__(self, *, database: Database, scans: ScanRepository, audit: AuditRepository) -> None:
        """Store injected dependencies."""
        self.database = database
        self.scans = scans
        self.audit = audit

    def purge(self, older_than_days: int) -> dict[str, Any]:
        """Compute cutoff, purge matching scans (and their dependents), log to audit, return summary.
        audit_log rows are never deleted.
        """
        cutoff = (datetime.now(UTC) - timedelta(days=older_than_days)).isoformat()
        with self.database.connect() as conn:
            ids = self.scans.ids_older_than(conn, cutoff)
            for scan_id in ids:
                self.scans.purge_scan(conn, scan_id)
            self.audit.append(
                conn,
                event="scans_purged",
                actor="system",
                detail={"purged_scans": len(ids), "older_than_days": older_than_days},
            )
        return {
            "purged_scans": len(ids),
            "older_than_days": older_than_days,
            "cutoff": cutoff,
        }
