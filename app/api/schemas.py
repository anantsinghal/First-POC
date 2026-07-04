from __future__ import annotations

from pydantic import BaseModel, Field


class FeedbackInput(BaseModel):
    label: str = Field(pattern="^(real_issue|false_positive|valid_exception|needs_review)$")
    note: str = Field(default="", max_length=1000)
    reviewer: str = Field(default="local-reviewer", min_length=1, max_length=120)


class SalesforceScanInput(BaseModel):
    object_name: str = Field(default="Opportunity", min_length=1, max_length=80)
    max_records: int = Field(default=500, ge=1, le=2000)


class BulkScanInput(BaseModel):
    object_name: str = Field(default="Opportunity", min_length=1, max_length=80)
    max_records: int = Field(default=100_000, ge=1, le=1_000_000)


class OrgScanInput(BaseModel):
    max_objects: int | None = Field(default=25, ge=1, le=500)
    stream_threshold: int = Field(default=10_000, ge=1)
    include: list[str] | None = None
    exclude: list[str] | None = None
    api_budget: int | None = Field(default=None, ge=1)
