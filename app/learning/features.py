from __future__ import annotations

from typing import Any

FEATURE_VERSION = 1

KNOWN_FINDING_TYPES: tuple[str, ...] = tuple(sorted([
    "required_missing",
    "picklist_invalid",
    "reference_missing",
    "invalid_format",
    "invalid_stage",
    "missing_account",
    "missing_required",
    "missing_contract_closed_won",
    "non_positive_amount",
    "stale_open_opportunity",
    "missing_close_date",
    "duplicate_name",
    "numeric_outlier",
    "robust_outlier",
    "rare_combination",
    "pattern_break",
    "ml_anomaly",
    "orphan_reference",
    "missing_children",
]))

SEVERITY_ORD = {"high": 3.0, "medium": 2.0, "low": 1.0}


def finding_features(finding: dict[str, Any]) -> dict[str, float]:
    """Build a flat numeric feature dict from a serialised finding."""
    evidence = finding.get("evidence") or {}
    features: dict[str, float] = {
        "sev_ord": SEVERITY_ORD.get(finding.get("severity"), 2.0),
        "confidence": float(finding.get("confidence") or 0.0),
        "priority": float(finding.get("priority") or 0.0),
        "has_field": 1.0 if finding.get("field_name") else 0.0,
    }
    finding_type = finding.get("finding_type")
    for t in KNOWN_FINDING_TYPES:
        features[f"type={t}"] = 1.0 if finding_type == t else 0.0

    top_contributors = evidence.get("top_contributors") or []
    max_absz = max((abs(c.get("z", 0.0)) for c in top_contributors), default=0.0)
    features["ev_anomaly_score"] = abs(float(evidence.get("anomaly_score", 0.0) or 0.0))
    features["ev_max_absz"] = float(max_absz)
    features["ev_modified_z"] = abs(float(evidence.get("modified_z", 0.0) or 0.0))
    features["ev_lift"] = float(evidence.get("lift", 0.0) or 0.0)
    return features


def feature_names() -> list[str]:
    """Deterministic ordered feature-name list (fixed columns)."""
    names = ["sev_ord", "confidence", "priority", "has_field"]
    names.extend(f"type={t}" for t in KNOWN_FINDING_TYPES)
    names.extend(["ev_anomaly_score", "ev_max_absz", "ev_modified_z", "ev_lift"])
    return names


def vectorize(finding: dict[str, Any]) -> list[float]:
    """finding_features -> list in feature_names() order."""
    features = finding_features(finding)
    return [features.get(name, 0.0) for name in feature_names()]
