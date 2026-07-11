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
        """Compute cutoff, purge matching scans (and their dependents), prune orphaned ledger
        state, log to audit, return summary. audit_log rows are never deleted.
        """
        cutoff = (datetime.now(UTC) - timedelta(days=older_than_days)).isoformat()
        with self.database.connect() as conn:
            ids = self.scans.ids_older_than(conn, cutoff)
            for scan_id in ids:
                self.scans.purge_scan(conn, scan_id)
            # Drop record_ledger / sync_state rows whose (source_key, object_name) no longer
            # has any remaining scan_manifests row (NOT EXISTS cleanup).
            cur_ledger = conn.execute(
                """DELETE FROM record_ledger
                   WHERE NOT EXISTS (
                     SELECT 1 FROM scan_manifests m
                     WHERE m.source_key = record_ledger.source_key
                       AND m.object_name = record_ledger.object_name
                   )"""
            )
            cur_sync = conn.execute(
                """DELETE FROM sync_state
                   WHERE NOT EXISTS (
                     SELECT 1 FROM scan_manifests m
                     WHERE m.source_key = sync_state.source_key
                       AND m.object_name = sync_state.object_name
                   )"""
            )
            ledger_pruned = int(cur_ledger.rowcount or 0) + int(cur_sync.rowcount or 0)
            self.audit.append(
                conn,
                event="scans_purged",
                actor="system",
                detail={
                    "purged_scans": len(ids),
                    "older_than_days": older_than_days,
                    "ledger_pruned": ledger_pruned,
                },
            )
        return {
            "purged_scans": len(ids),
            "older_than_days": older_than_days,
            "cutoff": cutoff,
            "ledger_pruned": ledger_pruned,
        }
