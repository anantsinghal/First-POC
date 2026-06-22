# Phase 14 — Pattern Miner (association-rule mining)

Blueprint Epic 6 (`docs/blueprint/13-mvp-build-plan.md`) + finding type `pattern_break`
(`docs/blueprint/10-data-models.md`). Adds a detector that LEARNS implicit expectations from the data
(support/confidence/lift) and flags records that break strong patterns — e.g. *"96% of Closed Won
Enterprise opportunities have Contract__c; this one doesn't."* Hand-rolled (NO new dependencies),
fully explainable. Pure additive Strategy — do not change services/persistence/api.

Guardrails (from `docs/blueprint/15-architecture-review-and-hardening-plan.md`): learned patterns are
**candidates, not rules**; refuse tiny datasets; every finding carries its support/confidence/lift and
a human-readable reason; never auto-activate.

## 1. Engine extension — `app/detection/base.py`
Patterns/ML need a PER-FINDING confidence (evidence strength), not a fixed policy value. Extend
`make_candidate` with backward-compatible keyword overrides:
`def make_candidate(ctx, record, rule, title, field_name, evidence, *, confidence: float | None =
None, priority: float | None = None) -> Candidate`. Look up `sev, def_conf, def_pri =
ctx.policy.severity_for(rule)`; use `confidence if confidence is not None else def_conf`, and
`priority if priority is not None else def_pri`. Severity stays policy-driven. Existing detectors are
unchanged (they pass no overrides).

## 2. Policy — `app/policy/field_policy.py`
Add `"pattern_break": (Severity.MEDIUM, 0.80, 0.70)` (default; the detector overrides confidence
per finding).

## 3. Detector — `app/detection/patterns/__init__.py` (empty) and
`app/detection/patterns/association.py` — `class AssociationPatternDetector`
- `id: str = "pattern_break"`; `requirement: ClassVar[CheckRequirement | None] = None`.
- `__init__(self, *, min_records: int = 40, min_support_count: int = 15, min_confidence: float = 0.9,
  min_lift: float = 1.0, max_antecedent_size: int = 2, categorical_max_distinct: int = 12)`.
- `detect(ctx)`:
  1. `records = [r for r in ctx.records if clean(r.get("Id"))]`; if `len(records) < self.min_records`
     return `[]` (tiny-dataset guardrail).
  2. **Categorical items**: for each field in `ctx.fields` excluding `Id`/`Name`, collect cleaned
     non-empty values; if `1 < distinct_count <= categorical_max_distinct`, each `(field, value)` is an
     antecedent item.
  3. **Consequent fields**: fields whose presence varies — i.e. among `records`, some have the field
     non-empty and some empty (`0 < null_rate < 1`). Consequent = "this field is present".
  4. **Mine rules**: for each consequent field C, for each antecedent A = a combination of 1..
     `max_antecedent_size` categorical items (all items from DIFFERENT fields):
     - `support_count` = #records matching all items in A; skip if `< min_support_count`.
     - `conf` = (#records matching A AND C present) / support_count.
     - `base_rate` = (#records with C present) / len(records); `lift = conf / base_rate` if base_rate>0.
     - Keep the rule as STRONG if `conf >= min_confidence` and `lift >= min_lift` and `conf > base_rate`.
  5. **Flag breakers**: for each strong rule, the records matching A but with C **absent** are breakers.
     For each breaker, build a candidate. If a record breaks several rules for the SAME consequent
     field, keep only the strongest (highest conf) to avoid noise.
  6. `make_candidate(ctx, record, rule="pattern_break", title="Record breaks a learned data pattern",
     field_name=<consequent field C>, evidence={...}, confidence=round(conf, 2))` where evidence =
     `{"method": "association rule mining", "antecedent": {field: value, ...}, "consequent_field": C,
     "expected": "present", "observed": "absent", "support": support_count, "confidence": round(conf,3),
     "lift": round(lift, 2), "population": len(records), "why": f"{round(conf*100)}% of records where
     <antecedent readable> have {C} populated; this one does not. Learned pattern — human review
     required."}`.
- Keep it bounded: antecedent items only from categorical fields (size ≤ 2); consequents only the
  varying-presence fields. This keeps the search small and the output explainable.

## 4. Registry — `app/detection/registry.py`
APPEND `AssociationPatternDetector()` to `build_default_registry()` (after the existing detectors).

## 5. Synthetic fixture — `data/demo_opportunities_large.csv`
Generate ~150 Opportunity rows, SAME columns as `data/demo_opportunities.csv`
(`Id,Name,StageName,Amount,AccountId,Contract__c,Segment__c,CloseDate`), realistic distribution, with
an INJECTED strong pattern: among `StageName=Closed Won` AND `Segment__c=Enterprise` rows (make this
cohort ≥ 30 rows), ~95% have `Contract__c` populated and ~5% (about 3–5 rows) are deliberate breakers
(blank Contract__c). Include normal variety elsewhere and 2–3 extreme `Amount` values (reused by
Phase 15). Unique 18-char-style Ids. This file is BOTH a test fixture and uploadable via the existing
`/api/scans/upload` so the user can see pattern/ML findings.

## 6. Tests — `tests/test_pattern_miner.py` (NEW)
- Build a synthetic in-memory list of ~120 records where antecedent `StageName=Closed Won` +
  `Segment__c=Enterprise` ⇒ `Contract__c` present in ~95% with exactly N breakers; assert
  `AssociationPatternDetector().detect(ctx)` returns N candidates, all `finding_type=="pattern_break"`,
  `field_name=="Contract__c"`, each `confidence >= 0.9`, evidence has `antecedent`, `support`, `lift`.
- Tiny dataset (< `min_records`) ⇒ `[]`.
- No strong rule (consequent present at the base rate everywhere) ⇒ `[]`.
- Regression: loading the 11-row `data/demo_opportunities.csv` and running
  `build_default_registry().detect(ctx)` still yields the SAME count as before (pattern_break fires 0
  because 11 < min_records); assert no `pattern_break` in the demo findings.

## Constraints
- NO new dependencies (stdlib + existing only). `from __future__ import annotations`; type hints;
  docstrings. detection imports only `app.domain.*` + `app.policy.*`.
- Do NOT modify `tests/test_scanner.py`, `tests/test_detection.py`, `tests/test_new_detectors.py`, or
  any service/persistence/api file.

## Done criteria
- `python -m pytest -q` green. Report files created/changed + the demo finding count (must be unchanged)
  and the large-fixture pattern_break count when uploaded.
