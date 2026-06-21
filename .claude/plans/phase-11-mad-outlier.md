# Phase 11 — Robust MAD outlier detector

Add a second, robust statistical Strategy: median + MAD (median absolute deviation) modified
z-score. It complements the IQR detector — on data with a heavy outlier it corroborates IQR with a
high-confidence, scale-robust signal. Pure OCP addition: new detector class + policy row + registry
append.

## Policy — `app/policy/field_policy.py`
Add to `severity_for`: `"robust_outlier": (Severity.MEDIUM, 0.82, 0.60)` (keep all existing entries).

## Detector — `app/detection/statistical/mad_outlier.py` — `class MadOutlierDetector`
- `id: str = "robust_outlier"`; `requirement: ClassVar[CheckRequirement | None] = None` (scans
  whatever numeric fields exist).
- `__init__(self, threshold: float = 3.5)` — store; standard modified-z cutoff.
- `detect(ctx)`: for each field in `ctx.fields`: `values = numeric_values(ctx.records, field)`; if
  `len(values) < 8` skip; `med = statistics.median(values)`; `mad = statistics.median([abs(v - med)
  for v in values])`; if `mad <= 0` skip. For each record: `v = parse_float(record.get(field))`; skip
  None; `mz = 0.6745 * (v - med) / mad`; if `abs(mz) > self.threshold` emit candidate:
  title `f"Unusual {field} value (robust)"`, field_name `field`, evidence
  `{"observed_value": v, "method": "MAD modified z-score", "median": round(med, 2), "mad":
  round(mad, 2), "modified_z": round(mz, 2), "threshold": self.threshold, "why": "The value is far
  from the median relative to the median absolute deviation (robust to skew)."}`.
- Imports: `statistics`, `ClassVar`, `numeric_values`/`parse_float` from `app.domain.values`,
  `make_candidate`/`Candidate`/`DetectionContext` from `..base`, `CheckRequirement` from
  `app.domain.models`.

## Registry — `app/detection/registry.py`
Insert `MadOutlierDetector()` immediately AFTER `IqrOutlierDetector()` (group the statistical
detectors). All other entries unchanged.

## Tests
`tests/test_robust_outlier.py` (NEW):
- Build a DetectionContext over an "Amount" column with 9 tight values around 100 plus one 100000;
  `MadOutlierDetector().detect(ctx)` -> exactly 1 candidate, finding_type "robust_outlier", evidence
  has `method == "MAD modified z-score"` and `modified_z` present.
- All-equal column (e.g. eight identical values) -> 0 candidates (mad == 0 path).
- Fewer than 8 numeric values -> 0 candidates.

Update `tests/test_new_detectors.py` regression guard: the demo via `build_default_registry()` now
yields **10** candidates (the 2,500,000 Amount is flagged by BOTH `numeric_outlier` and the new
`robust_outlier`); assert total == 10, `robust_outlier` appears exactly once, and the three types
{"stale_open_opportunity","duplicate_name","missing_close_date"} are still absent on the demo (as of
2026-06-21).

## Constraints
- Do NOT modify `tests/test_scanner.py` (still asserts `finding_count >= 5`; 10 satisfies it) or
  `tests/test_detection.py`.
- `from __future__ import annotations`; type hints; docstrings. detection imports domain+policy only.

## Done criteria
- `python -m pytest -q` green. Report files changed + results, including the demo's new finding count.
