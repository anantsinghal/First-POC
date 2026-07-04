from __future__ import annotations

from typing import TYPE_CHECKING

from ..persistence.database import Database
from ..persistence.repositories import FeedbackRepository

if TYPE_CHECKING:
    from .learning_service import LearningService


class RankingService:
    """Feedback-driven re-ranking for findings: a trained model when one exists, else the heuristic."""

    def __init__(
        self,
        *,
        database: Database,
        feedback: FeedbackRepository,
        min_labels: int = 5,
        weight: float = 0.3,
        learner: "LearningService | None" = None,
    ) -> None:
        self.database = database
        self.feedback = feedback
        self.min_labels = min_labels
        self.weight = weight
        self.learner = learner

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
        """Add rank_score (+ basis-specific keys), then sort by rank_score descending.

        Uses the trained model when one exists (ranking_basis="model"); otherwise falls back to
        the exact heuristic (ranking_basis="heuristic") - a data-starved model must never rank.
        """
        preds = self.learner.predict_acceptance(findings) if self.learner is not None else None

        if preds is not None:
            for row in findings:
                pred = preds[row["id"]]
                row["rank_score"] = round(
                    min(1.0, max(0.0, row["priority"] + self.weight * (pred["p_accept"] - 0.5))),
                    4,
                )
                row["model_p_accept"] = pred["p_accept"]
                row["model_factors"] = pred["top_factors"]
                row["ranking_basis"] = "model"
            return sorted(findings, key=lambda row: row["rank_score"], reverse=True)

        acceptance_by_type = self.type_acceptance()
        for row in findings:
            ranked_type = acceptance_by_type.get(row["finding_type"])
            if ranked_type is None:
                row["rank_score"] = row["priority"]
                row["type_acceptance"] = None
            else:
                acceptance = ranked_type["acceptance"]
                row["rank_score"] = round(
                    min(1.0, max(0.0, row["priority"] + self.weight * (acceptance - 0.5))),
                    4,
                )
                row["type_acceptance"] = acceptance
            row["ranking_basis"] = "heuristic"
        return sorted(findings, key=lambda row: row["rank_score"], reverse=True)
