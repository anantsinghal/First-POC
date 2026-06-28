from __future__ import annotations

from typing import Any

from ..persistence.database import Database
from ..persistence.repositories import FeedbackRepository, FindingRepository


class MetricsService:
    """Computes review/trust metrics across all findings + feedback.

    These make the feedback loop measurable: as reviewers label findings, the
    false-positive rate should fall and the acceptance rate should rise over time.
    """

    def __init__(self, *, database: Database, findings: FindingRepository, feedback: FeedbackRepository) -> None:
        """Store injected deps."""
        self.database = database
        self.findings = findings
        self.feedback = feedback

    def overview(self) -> dict[str, Any]:
        """Return aggregate counts + acceptance / false-positive rates."""
        with self.database.connect() as conn:
            by_status = self.findings.status_counts(conn)
            by_severity = self.findings.severity_counts(conn)
            by_type = self.findings.type_counts(conn)
            by_label = self.feedback.label_counts(conn)

        findings_total = sum(by_status.values())
        open_findings = by_status.get("open", 0) + by_status.get("reopened", 0)
        feedback_total = sum(by_label.values())
        real_issue = by_label.get("real_issue", 0)
        false_positive = by_label.get("false_positive", 0)

        acceptance_rate = round(real_issue / feedback_total, 3) if feedback_total else None
        false_positive_rate = round(false_positive / feedback_total, 3) if feedback_total else None

        return {
            "findings_total": findings_total,
            "open_findings": open_findings,
            "suppressed": by_status.get("suppressed_until", 0),
            "acknowledged": by_status.get("acknowledged", 0),
            "resolved": by_status.get("resolved", 0),
            "by_status": by_status,
            "by_severity": by_severity,
            "by_type": by_type,
            "feedback_total": feedback_total,
            "by_label": by_label,
            "acceptance_rate": acceptance_rate,
            "false_positive_rate": false_positive_rate,
        }
