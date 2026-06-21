from __future__ import annotations

from ..persistence.database import Database
from ..persistence.repositories import FindingRepository, ScanRepository

from .scan_service import ScanService


class DashboardService:
    """Computes dashboard stats using repos + delegates to scan_service for latest_scan detail."""

    def __init__(
        self,
        *,
        database: Database,
        scans: ScanRepository,
        findings: FindingRepository,
        scan_service: ScanService,
    ) -> None:
        """Store injected dependencies (note: circular-ish but ScanService passed in; wiring in main)."""
        self.database = database
        self.scans = scans
        self.findings = findings
        self.scan_service = scan_service

    def dashboard(self) -> dict:
        """Return counts + latest scan detail (or None). Matches baseline dashboard shape."""
        with self.database.connect() as conn:
            latest = self.scans.latest_id(conn)
            total = self.scans.count(conn)
            open_findings = self.findings.count_open(conn)
        latest_scan = self.scan_service.get_scan_detail(latest) if latest else None
        return {
            "total_scans": total,
            "open_findings": open_findings,
            "latest_scan": latest_scan,
        }
