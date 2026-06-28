from __future__ import annotations

from ..persistence.database import Database
from ..persistence.repositories import AuditRepository, FeedbackRepository, FindingRepository

from .errors import NotFoundError


class FindingService:
    """Handles listing findings (with attached feedback) and adding feedback (state transitions)."""

    def __init__(
        self,
        *,
        database: Database,
        findings: FindingRepository,
        feedback: FeedbackRepository,
        audit: AuditRepository | None = None,
    ) -> None:
        """Store injected dependencies."""
        self.database = database
        self.findings = findings
        self.feedback = feedback
        self.audit = audit

    def list_findings(self, scan_id: str | None = None, status: str | None = None) -> list[dict]:
        """List findings, attaching feedback list for each (matches baseline list_findings behavior)."""
        with self.database.connect() as conn:
            rows = self.findings.list(conn, scan_id=scan_id, status=status)
            for row in rows:
                row["feedback"] = self.feedback.list_for_finding(conn, row["id"])
            return rows

    def add_feedback(self, finding_id: str, label: str, note: str, reviewer: str) -> dict:
        """Map label to status, insert feedback, update finding status. Raises NotFoundError if finding missing."""
        state = {
            "real_issue": "acknowledged",
            "false_positive": "suppressed_until",
            "valid_exception": "suppressed_until",
            "needs_review": "open",
        }[label]
        with self.database.connect() as conn:
            if not self.findings.exists(conn, finding_id):
                raise NotFoundError("Finding not found")
            self.feedback.insert(
                conn, finding_id=finding_id, label=label, note=note, reviewer=reviewer
            )
            self.findings.set_status(conn, finding_id, state)
            if self.audit is not None:
                self.audit.append(
                    conn,
                    event="feedback_added",
                    actor=reviewer,
                    detail={"finding_id": finding_id, "label": label, "new_status": state},
                )
        return {"finding_id": finding_id, "status": state, "label": label}
