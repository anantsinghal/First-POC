from __future__ import annotations

from app.detection.base import DetectionContext
from app.detection.statistical.mad_outlier import MadOutlierDetector
from app.policy.field_policy import DEFAULT_POLICY


def test_mad_outlier_detects_large_deviation():
    """Tight cluster around 100 + one 100000 -> exactly 1 robust outlier."""
    amounts = [100, 101, 99, 100, 102, 98, 100, 101, 99] + [100000]
    records: list[dict[str, str]] = [
        {"Id": str(i), "Amount": str(a)} for i, a in enumerate(amounts)
    ]
    ctx = DetectionContext(
        object_name="X",
        records=records,
        fields=["Id", "Amount"],
        policy=DEFAULT_POLICY,
    )
    cands = MadOutlierDetector().detect(ctx)
    assert len(cands) == 1
    assert cands[0].finding_type == "robust_outlier"
    assert cands[0].field_name == "Amount"
    assert cands[0].evidence["method"] == "MAD modified z-score"
    assert "modified_z" in cands[0].evidence


def test_mad_outlier_all_equal_skips():
    """All equal values -> mad==0 -> 0 candidates."""
    records: list[dict[str, str]] = [
        {"Id": str(i), "Amount": "100"} for i in range(8)
    ]
    ctx = DetectionContext(
        object_name="X",
        records=records,
        fields=["Id", "Amount"],
        policy=DEFAULT_POLICY,
    )
    cands = MadOutlierDetector().detect(ctx)
    assert len(cands) == 0


def test_mad_outlier_fewer_than_8_skips():
    """<8 numeric values -> 0 candidates."""
    records: list[dict[str, str]] = [
        {"Id": str(i), "Amount": str(100 + i)} for i in range(7)
    ]
    ctx = DetectionContext(
        object_name="X",
        records=records,
        fields=["Id", "Amount"],
        policy=DEFAULT_POLICY,
    )
    cands = MadOutlierDetector().detect(ctx)
    assert len(cands) == 0
