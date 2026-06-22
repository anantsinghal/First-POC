from __future__ import annotations

from datetime import date
from typing import ClassVar

from ...domain.models import CheckRequirement
from ...domain.values import clean
from ...policy.rules import Rule, evaluate_condition, load_rule_catalog
from ..base import Candidate, DetectionContext, make_candidate


class RuleInterpreterDetector:
    """Generic rule interpreter that executes declarative rules from the catalog.
    id is 'rule_interpreter'; actual finding_type comes from each Rule.
    """

    id: str = "rule_interpreter"
    requirement: ClassVar[CheckRequirement | None] = None

    def __init__(self, rules: list[Rule], *, today: date | None = None) -> None:
        self.rules = rules or []
        self._today_value = today

    def _today(self) -> date:
        return self._today_value or date.today()

    def detect(self, ctx: DetectionContext) -> list[Candidate]:
        candidates: list[Candidate] = []
        today = self._today()
        for rule in self.rules:
            if rule.object_name.lower() != ctx.object_name.lower():
                continue
            ref_fields = rule.referenced_fields()
            if not all(f in ctx.fields for f in ref_fields):
                continue  # skip rule if any referenced field absent
            for record in ctx.records:
                if not clean(record.get("Id")):
                    continue
                # check all when conditions
                when_ok = all(
                    evaluate_condition(record, cond, today) for cond in rule.when
                )
                if not when_ok:
                    continue
                # violation = all when true AND (expect is None OR not expect)
                if rule.expect is None:
                    violates = True
                else:
                    violates = not evaluate_condition(record, rule.expect, today)
                if not violates:
                    continue  # not a violation
                # violation
                candidates.append(
                    make_candidate(
                        ctx,
                        record,
                        rule=rule.finding_type,
                        title=rule.title,
                        field_name=rule.field_name,
                        evidence={
                            "method": "rule catalog",
                            "rule_id": rule.rule_id,
                            "matched": {c.field: clean(record.get(c.field)) for c in rule.when},
                            "observed_value": clean(record.get(rule.field_name)) if rule.field_name else None,
                            "why": rule.explanation,
                        },
                    )
                )
        return candidates

    def coverage_requirements(self) -> list[CheckRequirement]:
        reqs: list[CheckRequirement] = []
        for rule in self.rules:
            reqs.append(
                CheckRequirement(
                    label=f"{rule.title} (rule)",
                    requires=tuple(sorted(rule.referenced_fields())),
                    object_scope=rule.object_name.lower(),
                )
            )
        return reqs
