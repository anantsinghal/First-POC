from __future__ import annotations

from ..persistence.database import Database
from ..persistence.repositories import FeedbackRepository


class RankingService:
    """Feedback-driven re-ranking for findings."""

    def __init__(
        self,
        *,
        database: Database,
        feedback: FeedbackRepository,
        min_labels: int = 5,
        weight: float = 0.3,
    ) -> None:
        self.database = database
        self.feedback = feedback
        self.min_labels = min_labels
        self.weight = weight

    def type_acceptance(self) -> dict[str, dict]:
        """Return smoothed acceptance by finding type for sufficiently labeled types."""
        with self.database.connect() as conn:
            counts = self.feedback.label_counts_by_type(conn)

        acceptance_by_type: dict[str, dict] = {}
        for finding_type, labels in counts.items():
            accepted = labels.get("real_issue", 0)
            rejected = labels.get("false_positive", 0) + labels.get("valid_exception", 0)
            total = accepted + rejected
            if total < self.min_labels:
                continue
            acceptance_by_type[finding_type] = {
                "acceptance": round((accepted + 1) / (total + 2), 3),
                "labels": total,
            }
        return acceptance_by_type

    def rank(self, findings: list[dict]) -> list[dict]:
        """Add rank_score and type_acceptance, then sort by rank_score descending."""
        acceptance_by_type = self.type_acceptance()
        for row in findings:
            ranked_type = acceptance_by_type.get(row["finding_type"])
            if ranked_type is None:
                row["rank_score"] = row["priority"]
                row["type_acceptance"] = None
                continue
            acceptance = ranked_type["acceptance"]
            row["rank_score"] = round(
                min(1.0, max(0.0, row["priority"] + self.weight * (acceptance - 0.5))),
                4,
            )
            row["type_acceptance"] = acceptance
        return sorted(findings, key=lambda row: row["rank_score"], reverse=True)
