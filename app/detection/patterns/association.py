from __future__ import annotations

from collections import defaultdict
from itertools import combinations
from typing import ClassVar

from ...domain.models import CheckRequirement
from ...domain.values import clean
from ..base import Candidate, DetectionContext, make_candidate


class AssociationPatternDetector:
    """Association-rule pattern miner that learns strong (support/conf/lift) categorical patterns and flags breakers as 'pattern_break'."""

    id: str = "pattern_break"
    requirement: ClassVar[CheckRequirement | None] = None

    def __init__(
        self,
        *,
        min_records: int = 40,
        min_support_count: int = 15,
        min_confidence: float = 0.9,
        min_lift: float = 1.1,
        min_margin: float = 0.10,
        max_antecedent_size: int = 2,
        categorical_max_distinct: int = 12,
    ) -> None:
        """Hyperparams for mining; guardrails prevent noise on small or weak data. min_margin requires absolute lift over base rate."""
        self.min_records = min_records
        self.min_support_count = min_support_count
        self.min_confidence = min_confidence
        self.min_lift = min_lift
        self.min_margin = min_margin
        self.max_antecedent_size = max_antecedent_size
        self.categorical_max_distinct = categorical_max_distinct

    def detect(self, ctx: DetectionContext) -> list[Candidate]:
        """Mine rules on categorical presence patterns; flag breakers of strong rules with per-finding confidence."""
        records = [r for r in ctx.records if clean(r.get("Id"))]
        if len(records) < self.min_records:
            return []

        # Build categorical fields/items
        field_values: dict[str, list[str]] = {}
        for field in ctx.fields:
            if field in ("Id", "Name"):
                continue
            vals = [clean(rec.get(field)) for rec in records if clean(rec.get(field))]
            distinct = set(vals)
            if 1 < len(distinct) <= self.categorical_max_distinct:
                field_values[field] = sorted(distinct)

        # Consequent fields: varying presence (some present, some absent)
        cons_fields: list[str] = []
        n = len(records)
        for field in ctx.fields:
            if field == "Id":
                continue
            non_empty = sum(1 for r in records if clean(r.get(field)))
            null_rate = (n - non_empty) / n if n > 0 else 0
            if 0 < null_rate < 1:
                cons_fields.append(field)

        # Mine strong rules
        items = [(f, v) for f, vs in field_values.items() for v in vs]
        strong_rules: list[tuple[list[tuple[str, str]], str, int, float, float]] = []  # ante, C, supp, conf, lift

        for k in range(1, self.max_antecedent_size + 1):
            for ante_tuple in combinations(items, k):
                fields_in_ante = [a[0] for a in ante_tuple]
                if len(set(fields_in_ante)) != k:
                    continue  # must be from distinct fields
                ante = list(ante_tuple)

                # support_count for ante
                support_count = 0
                for rec in records:
                    if all(clean(rec.get(f)) == v for f, v in ante):
                        support_count += 1
                if support_count < self.min_support_count:
                    continue

                for C in cons_fields:
                    # conf count: ante match + C present
                    conf_count = 0
                    for rec in records:
                        if all(clean(rec.get(f)) == v for f, v in ante) and clean(rec.get(C)):
                            conf_count += 1
                    conf = conf_count / support_count if support_count > 0 else 0.0

                    base_count = sum(1 for r in records if clean(r.get(C)))
                    base_rate = base_count / n if n > 0 else 0.0
                    lift = conf / base_rate if base_rate > 0 else 0.0

                    if conf >= self.min_confidence and lift >= self.min_lift and conf > base_rate and (conf - base_rate) >= self.min_margin:
                        strong_rules.append((ante, C, support_count, conf, lift))

        # Find breakers and keep strongest per record per consequent
        record_breaks: dict[str, dict[str, list[tuple[float, list[tuple[str,str]], int, float, dict[str,str]]]]] = defaultdict(lambda: defaultdict(list))
        for ante, C, supp, conf, lift in strong_rules:
            for rec in records:
                if not all(clean(rec.get(f)) == v for f, v in ante):
                    continue
                if clean(rec.get(C)):
                    continue  # not breaker
                rid = clean(rec.get("Id"))
                record_breaks[rid][C].append((conf, ante, supp, lift, rec))

        # select best per (rid, C)
        final: list[tuple[dict[str,str], str, list[tuple[str,str]], int, float, float]] = []
        for rid, cdict in record_breaks.items():
            for C, lst in cdict.items():
                if not lst:
                    continue
                lst.sort(key=lambda x: x[0], reverse=True)
                best = lst[0]
                conf, ante, supp, lift, rec = best
                final.append((rec, C, ante, supp, conf, lift))

        # Build candidates
        candidates: list[Candidate] = []
        for rec, C, ante, supp, conf, lift in final:
            ante_dict = {f: v for f, v in ante}
            ante_readable = " and ".join(f"{f}={v}" for f, v in ante)
            evidence = {
                "method": "association rule mining",
                "antecedent": ante_dict,
                "consequent_field": C,
                "expected": "present",
                "observed": "absent",
                "support": supp,
                "confidence": round(conf, 3),
                "lift": round(lift, 2),
                "population": len(records),
                "why": f"{round(conf*100)}% of records where {ante_readable} have {C} populated; this one does not. Learned pattern — human review required.",
            }
            cand = make_candidate(
                ctx,
                rec,
                rule="pattern_break",
                title="Record breaks a learned data pattern",
                field_name=C,
                evidence=evidence,
                confidence=round(conf, 2),
            )
            candidates.append(cand)

        return candidates
