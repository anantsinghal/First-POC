from __future__ import annotations

from enum import Enum


class Severity(str, Enum):
    """Severity levels for findings."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class CoverageGrade(str, Enum):
    """Coverage grades for scans."""

    GOOD = "good"
    PARTIAL = "partial"
    BLOCKED = "blocked"


class FindingStatus(str, Enum):
    """Lifecycle status of a finding."""

    OPEN = "open"
    REOPENED = "reopened"
    RESOLVED = "resolved"
    ACKNOWLEDGED = "acknowledged"
    SUPPRESSED_UNTIL = "suppressed_until"


class FeedbackLabel(str, Enum):
    """Human feedback labels on findings."""

    REAL_ISSUE = "real_issue"
    FALSE_POSITIVE = "false_positive"
    VALID_EXCEPTION = "valid_exception"
    NEEDS_REVIEW = "needs_review"
