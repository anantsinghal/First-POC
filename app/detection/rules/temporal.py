from __future__ import annotations

from datetime import date

from ...domain.values import clean, parse_date
from ..base import Candidate, DetectionContext, make_candidate


class StaleOpenOpportunityDetector:
    """Temporal detector for open opportunities with past CloseDate. id='stale_open_opportunity'."""

    id: str = "stale_open_opportunity"

    CLOSED_STAGES = frozenset({"Closed Won", "Closed Lost"})

    def __init__(self, today: date | None = None) -> None:
        """Allow injecting a fixed 'today' for tests; default lazy date.today() in _today."""
        self._today_value = today

    def _today(self) -> date:
        """Return injected today or call date.today() lazily (never at import time)."""
        return self._today_value or date.today()

    def detect(self, ctx: DetectionContext) -> list[Candidate]:
        """Only for Opportunity with CloseDate+StageName; emit for non-closed stages where parsed CloseDate < today."""
        if not (ctx.is_opportunity() and ctx.has("CloseDate") and ctx.has("StageName")):
            return []
        candidates: list[Candidate] = []
        today = self._today()
        for record in ctx.records:
            if not clean(record.get("Id")):
                continue
            stage = clean(record.get("StageName"))
            if stage in self.CLOSED_STAGES:
                continue
            parsed = parse_date(record.get("CloseDate"))
            if parsed is None:
                continue
            if parsed < today:
                candidates.append(
                    make_candidate(
                        ctx,
                        record,
                        rule="stale_open_opportunity",
                        title="Open Opportunity past its Close Date",
                        field_name="CloseDate",
                        evidence={
                            "observed_value": clean(record.get("CloseDate")),
                            "stage": stage,
                            "as_of": today.isoformat(),
                            "why": "Opportunity is still open but its CloseDate is in the past; the forecast or stage likely needs updating.",
                        },
                    )
                )
        return candidates
