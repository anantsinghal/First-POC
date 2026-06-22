from __future__ import annotations

from typing import ClassVar

from ...domain.models import CheckRequirement
from ...domain.values import clean
from ..base import Candidate, DetectionContext, make_candidate


class InvalidStageDetector:
    """Opportunity stage not in policy allowlist. id='invalid_stage'."""

    id: str = "invalid_stage"
    requirement: ClassVar[CheckRequirement | None] = CheckRequirement("Invalid-stage check", ("StageName",), "opportunity")

    def detect(self, ctx: DetectionContext) -> list[Candidate]:
        """Only for Opportunity; emit if StageName present, value truthy and not valid."""
        if not ctx.is_opportunity():
            return []
        candidates: list[Candidate] = []
        if not ctx.has("StageName"):
            return candidates
        for record in ctx.records:
            if not clean(record.get("Id")):
                continue
            stage = clean(record.get("StageName"))
            if stage and stage not in ctx.policy.valid_stages():
                candidates.append(
                    make_candidate(
                        ctx,
                        record,
                        rule="invalid_stage",
                        title="Unknown Opportunity stage",
                        field_name="StageName",
                        evidence={
                            "observed_value": stage,
                            "allowed_values": sorted(ctx.policy.valid_stages()),
                            "why": "StageName is not in the POC stage allowlist.",
                        },
                    )
                )
        return candidates


class MissingAccountDetector:
    """Opportunity missing AccountId. id='missing_account'."""

    id: str = "missing_account"
    requirement: ClassVar[CheckRequirement | None] = CheckRequirement("Missing-account check", ("AccountId",), "opportunity")

    def detect(self, ctx: DetectionContext) -> list[Candidate]:
        """Only for Opportunity; emit when AccountId field present but empty value."""
        if not ctx.is_opportunity():
            return []
        candidates: list[Candidate] = []
        if not ctx.has("AccountId"):
            return candidates
        for record in ctx.records:
            if not clean(record.get("Id")):
                continue
            if not clean(record.get("AccountId")):
                candidates.append(
                    make_candidate(
                        ctx,
                        record,
                        rule="missing_account",
                        title="Opportunity has no Account",
                        field_name="AccountId",
                        evidence={
                            "observed_value": None,
                            "why": "The record has no parent AccountId in the extracted data.",
                        },
                    )
                )
        return candidates


