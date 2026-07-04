from __future__ import annotations

import re
from typing import ClassVar

from ...domain.models import CheckRequirement
from ...domain.schema import FieldType
from ...domain.values import clean
from ..base import Candidate, DetectionContext, make_candidate

# Module-level compiled heuristics (pragmatic, not RFC-exhaustive)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
URL_RE = re.compile(r"^https?://[^\s.]+\.[^\s]+$", re.IGNORECASE)
# Root-relative paths (e.g. "/services/images/photo/001...", "/resource/.../pic.png") are a
# normal, valid value for "url"-typed fields in many systems, incl. Salesforce's own PhotoUrl —
# resolved relative to the instance/host. Found via real-org testing (2026-07-04): without this,
# every such value was a false positive.
RELATIVE_PATH_RE = re.compile(r"^/[^\s]+$")


def _is_valid_url(value: str) -> bool:
    """Absolute http(s) URL or a root-relative path (both are valid 'url' field values)."""
    return bool(URL_RE.match(value) or RELATIVE_PATH_RE.match(value))


def _is_valid_phone(value: str) -> bool:
    """Phone valid if after stripping + - ( ) . space, all digits and len 7-15."""
    cleaned = re.sub(r"[+\-().\s]", "", value)
    return cleaned.isdigit() and 7 <= len(cleaned) <= 15


class FormatValidityDetector:
    """Schema-driven format validator for email/phone/url fields (no-op if no schema)."""

    id: str = "invalid_format"
    requirement: ClassVar[CheckRequirement | None] = None

    def detect(self, ctx: DetectionContext) -> list[Candidate]:
        if ctx.schema is None:
            return []
        candidates: list[Candidate] = []
        for fd in ctx.schema.format_fields():
            if fd.name not in ctx.fields:
                continue
            for record in ctx.records:
                if not clean(record.get("Id")):
                    continue
                value = clean(record.get(fd.name))
                if not value:
                    continue  # empty is required/completeness, not format
                valid = True
                if fd.data_type == FieldType.EMAIL:
                    valid = bool(EMAIL_RE.match(value))
                elif fd.data_type == FieldType.PHONE:
                    valid = _is_valid_phone(value)
                elif fd.data_type == FieldType.URL:
                    valid = _is_valid_url(value)
                if not valid:
                    candidates.append(
                        make_candidate(
                            ctx,
                            record,
                            rule="invalid_format",
                            title=f"Invalid {fd.name} format",
                            field_name=fd.name,
                            evidence={
                                "field": fd.name,
                                "data_type": fd.data_type.value,
                                "observed_value": value,
                                "why": f"{fd.name} value '{value}' does not look like a valid {fd.data_type.value}.",
                            },
                        )
                    )
        return candidates
