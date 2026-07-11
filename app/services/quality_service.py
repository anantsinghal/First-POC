from __future__ import annotations

import sqlite3
from typing import Any

from ..persistence.database import Database
from ..persistence.repositories import CoverageRepository, FindingRepository, ScanRepository

from .errors import NotFoundError


class QualityService:
    """Computes per-scan data quality and trend against the prior completed scan."""

    def __init__(
        self,
        *,
        database: Database,
        scans: ScanRepository,
        coverage_repo: CoverageRepository,
        findings: FindingRepository,
    ) -> None:
        """Store injected dependencies."""
        self.database = database
        self.scans = scans
        self.coverage_repo = coverage_repo
        self.findings = findings

    def _counts(self, conn: sqlite3.Connection, scan_id: str) -> tuple[int, int]:
        """Return (observed_records, flagged_records) for a scan from the stable snapshot.

        Falls back to live values for scans persisted before quality snapshots existed; the
        snapshot is what makes a previous scan's score trustworthy after later scans migrate
        findings' scan_id on re-detection.
        """
        snapshot = self.scans.get_quality(conn, scan_id)
        if snapshot is not None:
            return snapshot["observed_records"], snapshot["flagged_records"]
        coverage = self.coverage_repo.get(conn, scan_id)
        manifest = self.scans.get(conn, scan_id)
        observed = coverage["observed_records"] if coverage else (manifest or {}).get("row_count", 0)
        return observed, self.findings.distinct_record_count(conn, scan_id)

    def _score(self, conn: sqlite3.Connection, scan_id: str) -> float | None:
        """Return the quality score for a scan, or None when no observed records exist."""
        observed, flagged = self._counts(conn, scan_id)
        if not observed:
            return None
        return round(100 * round(1 - flagged / observed, 4), 1)

    def for_scan(self, scan_id: str) -> dict[str, Any]:
        """Return per-scan quality details and trend versus the prior completed scan."""
        with self.database.connect() as conn:
            manifest = self.scans.get(conn, scan_id)
            if manifest is None:
                raise NotFoundError("Scan not found")

            coverage = self.coverage_repo.get(conn, scan_id)
            grade = coverage["grade"] if coverage else None
            sampled = bool(
                coverage and any("record cap" in suppressed for suppressed in coverage.get("suppressed_checks", []))
            )

            observed, flagged = self._counts(conn, scan_id)
            findings_total = self.findings.count_for_scan(conn, scan_id)
            by_severity = self.findings.severity_counts(conn, scan_id=scan_id)

            clean_record_rate = round(1 - flagged / observed, 4) if observed else None
            quality_score = round(100 * clean_record_rate, 1) if clean_record_rate is not None else None

            previous_scan_id = self.scans.previous_scan_id(
                conn, manifest["object_name"], manifest["created_at"], manifest.get("source_key", "")
            )
            previous_quality_score = (
                self._score(conn, previous_scan_id) if previous_scan_id is not None else None
            )
            quality_delta = (
                round(quality_score - previous_quality_score, 1)
                if quality_score is not None and previous_quality_score is not None
                else None
            )

        return {
            "scan_id": scan_id,
            "object_name": manifest["object_name"],
            "observed_records": observed,
            "flagged_records": flagged,
            "findings_total": findings_total,
            "clean_record_rate": clean_record_rate,
            "quality_score": quality_score,
            "by_severity": by_severity,
            "coverage_grade": grade,
            "sampled": sampled,
            "previous_scan_id": previous_scan_id,
            "previous_quality_score": previous_quality_score,
            "quality_delta": quality_delta,
        }
