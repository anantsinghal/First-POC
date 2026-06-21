from __future__ import annotations

from ...domain.values import clean, parse_float
from ..base import Candidate, DetectionContext, make_candidate


class InvalidStageDetector:
    """Opportunity stage not in policy allowlist. id='invalid_stage'."""

    id: str = "invalid_stage"

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


class ClosedWonContractDetector:
    """Closed Won Opportunity missing Contract__c. id='missing_contract_closed_won'."""

    id: str = "missing_contract_closed_won"

    def detect(self, ctx: DetectionContext) -> list[Candidate]:
        """Only for Opportunity; requires both StageName+Contract__c fields."""
        if not ctx.is_opportunity():
            return []
        candidates: list[Candidate] = []
        if not (ctx.has("StageName") and ctx.has("Contract__c")):
            return candidates
        for record in ctx.records:
            if not clean(record.get("Id")):
                continue
            stage = clean(record.get("StageName"))
            if stage == "Closed Won" and not clean(record.get("Contract__c")):
                candidates.append(
                    make_candidate(
                        ctx,
                        record,
                        rule="missing_contract_closed_won",
                        title="Closed Won Opportunity has no contract",
                        field_name="Contract__c",
                        evidence={
                            "stage": stage,
                            "observed_value": None,
                            "why": "POC policy expects Contract__c for Closed Won opportunities.",
                        },
                    )
                )
        return candidates


class NonPositiveAmountDetector:
    """Opportunity Amount <= 0. id='non_positive_amount'."""

    id: str = "non_positive_amount"

    def detect(self, ctx: DetectionContext) -> list[Candidate]:
        """Only for Opportunity; use parse_float for comparison."""
        if not ctx.is_opportunity():
            return []
        candidates: list[Candidate] = []
        if not ctx.has("Amount"):
            return candidates
        for record in ctx.records:
            if not clean(record.get("Id")):
                continue
            amt = parse_float(record.get("Amount"))
            if amt is not None and amt <= 0:
                candidates.append(
                    make_candidate(
                        ctx,
                        record,
                        rule="non_positive_amount",
                        title="Opportunity Amount is zero or negative",
                        field_name="Amount",
                        evidence={
                            "observed_value": record.get("Amount"),
                            "why": "POC policy treats non-positive opportunity value as reviewable.",
                        },
                    )
                )
        return candidates
