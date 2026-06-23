from __future__ import annotations

from collections import Counter
from typing import ClassVar

from ...domain.models import CheckRequirement
from ...domain.values import clean
from ..base import Candidate, DetectionContext, make_candidate


class MissingChildrenDetector:
    """Detect parents with no expected children.

    Link-field is heuristically auto-discovered by Id overlap (no child schema
    required); assumes related_records contains the full child set for accurate
    counts.
    """

    id: str = "missing_children"
    requirement: ClassVar[CheckRequirement | None] = None

    def __init__(
        self,
        *,
        min_records: int = 10,
        min_coverage: float = 0.8,
        min_link_overlap: float = 0.5,
    ) -> None:
        self.min_records = min_records
        self.min_coverage = min_coverage
        self.min_link_overlap = min_link_overlap

    def detect(self, ctx: DetectionContext) -> list[Candidate]:
        if not ctx.related_records:
            return []

        parent_ids = {clean(r.get("Id")) for r in ctx.records if clean(r.get("Id"))}
        if len(parent_ids) < self.min_records:
            return []

        candidates: list[Candidate] = []
        for child_object, child_records in ctx.related_records.items():
            link_field = self._discover_link_field(child_records, parent_ids)
            if link_field is None:
                continue

            child_count: Counter[str] = Counter()
            for record in child_records:
                parent_id = clean(record.get(link_field))
                if parent_id in parent_ids:
                    child_count[parent_id] += 1

            parents_with_children = sum(1 for parent_id in parent_ids if child_count[parent_id] >= 1)
            coverage = parents_with_children / len(parent_ids)
            if coverage < self.min_coverage or parents_with_children == len(parent_ids):
                continue

            for record in ctx.records:
                parent_id = clean(record.get("Id"))
                if not parent_id or child_count[parent_id] >= 1:
                    continue
                candidates.append(
                    make_candidate(
                        ctx,
                        record,
                        rule="missing_children",
                        title=f"No related {child_object} records",
                        field_name=None,
                        evidence={
                            "method": "relationship completeness",
                            "child_object": child_object,
                            "link_field": link_field,
                            "coverage_rate": round(coverage, 3),
                            "child_count": 0,
                            "parent_population": len(parent_ids),
                            "why": f"{round(coverage*100)}% of {ctx.object_name} records have at least one related {child_object}; this one has none - possible missing child records.",
                        },
                    )
                )
        return candidates

    def _discover_link_field(
        self,
        child_records: list[dict[str, str]],
        parent_ids: set[str],
    ) -> str | None:
        best_field: str | None = None
        best_fraction = 0.0
        field_names = {
            field
            for record in child_records
            for field in record
            if field != "Id"
        }
        for field in field_names:
            values = [clean(record.get(field)) for record in child_records if clean(record.get(field))]
            if not values:
                continue
            overlap = sum(1 for value in values if value in parent_ids)
            fraction = overlap / len(values)
            if fraction > best_fraction:
                best_field = field
                best_fraction = fraction
        if best_fraction < self.min_link_overlap:
            return None
        return best_field
