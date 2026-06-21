from __future__ import annotations

from collections import Counter

from ...domain.values import clean
from ..base import Candidate, DetectionContext, make_candidate


class RareCombinationDetector:
    """Rare stage+segment combination detector. id='rare_combination'."""

    id: str = "rare_combination"

    def detect(self, ctx: DetectionContext) -> list[Candidate]:
        """Only when exactly StageName+Segment__c present and >=8 records."""
        category_fields = [f for f in ("StageName", "Segment__c") if ctx.has(f)]
        if len(category_fields) != 2 or len(ctx.records) < 8:
            return []
        combos = Counter(tuple(clean(r.get(f)) for f in category_fields) for r in ctx.records)
        candidates: list[Candidate] = []
        for record in ctx.records:
            combo = tuple(clean(record.get(f)) for f in category_fields)
            if all(combo) and combos[combo] == 1:
                candidates.append(
                    make_candidate(
                        ctx,
                        record,
                        rule="rare_combination",
                        title="Rare stage and segment combination",
                        field_name=", ".join(category_fields),
                        evidence={
                            "combination": dict(zip(category_fields, combo)),
                            "occurrences": 1,
                            "population": len(ctx.records),
                            "why": "This combination occurs once in the supplied scan. Human review is required.",
                        },
                    )
                )
        return candidates
