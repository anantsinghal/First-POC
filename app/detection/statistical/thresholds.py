from __future__ import annotations


IQR_MULTIPLIER = 1.5
MAD_SCALE = 0.6745
MAD_THRESHOLD = 3.5


def iqr_fences(q1: float, q3: float) -> tuple[float, float] | None:
    """Return 1.5xIQR Tukey fences, or None when the IQR is non-positive."""
    iqr = q3 - q1
    if iqr <= 0:
        return None
    return (q1 - IQR_MULTIPLIER * iqr, q3 + IQR_MULTIPLIER * iqr)


def mad_z(value: float, med: float, mad: float) -> float:
    """Return the modified z-score for a value relative to a median and MAD."""
    return MAD_SCALE * (value - med) / mad
