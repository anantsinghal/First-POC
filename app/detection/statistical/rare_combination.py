from __future__ import annotations

from collections import Counter
from typing import ClassVar

from ...domain.models import CheckRequirement
from ...domain.values import clean
from ..base import Candidate, DetectionContext, make_candidate


class RareCombinationDetector:
    """Rare stage+segment combination detector. id='rare_combination'."""

    id: str = "rare_combination"

    def __init__(self, *, category_fields: tuple[str, ...] = ("StageName", "Segment__c"), min_records: int = 8) -> None:
        self.category_fields = category_fields
        self.min_records = min_records

    def detect(self, ctx: DetectionContext) -> list[Candidate]:
        """Only when exactly the configured category fields present and >= min_records records."""
        category_fields = [f for f in self.category_fields if ctx.has(f)]
        if len(category_fields) != len(self.category_fields) or len(ctx.records) < self.min_records:
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

    def coverage_requirements(self) -> list[CheckRequirement]:
        return [CheckRequirement(
            label=f"Rare combination check ({', '.join(self.category_fields)})",
            requires=tuple(self.category_fields),
            object_scope="opportunity"
        )]
