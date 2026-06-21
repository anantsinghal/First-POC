# Phase 10 — Detector-driven coverage messaging

Today `CoverageGrader` hardcodes opportunity-specific suppressed-check strings and does NOT report
that the new CloseDate/Name detectors were skipped when their fields are absent. Make suppressed
checks **data-driven**: each detector declares the fields it needs; the grader consumes that metadata
and reports every detector that can't run. Grade/missing/reason logic stays IDENTICAL (parity); only
`suppressed_checks` content becomes comprehensive. Coverage must NOT import the detection layer — it
consumes a plain `CheckRequirement` domain object.

## 1. Domain — add to `app/domain/models.py`
```
@dataclass(frozen=True)
class CheckRequirement:
    """Declares the fields a detector needs and the object it applies to (for coverage reporting)."""
    label: str
    requires: tuple[str, ...]
    object_scope: str | None = None   # None = any object; else a lowercased object name
```

## 2. Detection — declare `requirement` on each detector
Add a class attribute `requirement: ClassVar[CheckRequirement | None]` to each detector (import
`CheckRequirement` from `app.domain.models`, `ClassVar` from typing). Values:
- `RequiredFieldsDetector`: `CheckRequirement("Required-field check", ("Name",), None)`
- `InvalidStageDetector`: `CheckRequirement("Invalid-stage check", ("StageName",), "opportunity")`
- `MissingAccountDetector`: `CheckRequirement("Missing-account check", ("AccountId",), "opportunity")`
- `ClosedWonContractDetector`: `CheckRequirement("Closed-Won-without-contract check",
  ("StageName", "Contract__c"), "opportunity")`
- `NonPositiveAmountDetector`: `CheckRequirement("Non-positive-amount check", ("Amount",), "opportunity")`
- `IqrOutlierDetector`: `requirement = None` (scans whatever numeric fields exist)
- `RareCombinationDetector`: `CheckRequirement("Rare stage/segment combination check",
  ("StageName", "Segment__c"), "opportunity")`
- `StaleOpenOpportunityDetector`: `CheckRequirement("Stale-open-opportunity check",
  ("StageName", "CloseDate"), "opportunity")`
- `DuplicateNameDetector`: `CheckRequirement("Duplicate-by-Name check", ("Name",), None)`
- `MissingCloseDateDetector`: `CheckRequirement("Missing-Close-Date check", ("CloseDate",), "opportunity")`

Do not change any detector's `detect()` logic.

## 3. Registry — `app/detection/registry.py`
Add `def requirements(self) -> list[CheckRequirement]`: return
`[d.requirement for d in self._detectors if getattr(d, "requirement", None) is not None]`
(preserve registration order).

## 4. Coverage — rewrite suppressed logic in `app/coverage/grader.py`
- `__init__(self, policy: FieldPolicy, requirements: Iterable[CheckRequirement] = ())` — store
  `self.requirements = tuple(requirements)`. (Default empty keeps construction backward compatible.)
- In `grade(...)`, keep `expected`, `missing`, grade, and reason EXACTLY as now. Replace the
  hardcoded opportunity suppressed block with:
  ```
  field_set = set(fields)
  suppressed: list[str] = []
  if "Id" in missing:
      suppressed.append("all record-level checks: Id is absent")
  for req in self.requirements:
      if req.object_scope is not None and req.object_scope != object_name.lower():
          continue
      missing_req = [f for f in req.requires if f not in field_set]
      if missing_req:
          suppressed.append(f"{req.label}: needs {', '.join(missing_req)}")
  ```
  (coverage imports `CheckRequirement` from `app.domain.models` only — NOT from detection.)

## 5. Composition — `app/api/container.py`
Build the registry first, then
`grader = CoverageGrader(DEFAULT_POLICY, requirements=registry.requirements())`.

## 6. Tests — `tests/test_profiling_coverage.py`
- Keep `test_coverage_grader_good_on_full_demo` (full demo -> GOOD, `missing_fields == []`,
  `suppressed_checks == []`). This still holds because every detector's required fields are present.
- Update `test_coverage_grader_partial_missing_expected_fields` to build the grader WITH requirements:
  `CoverageGrader(DEFAULT_POLICY, requirements=build_default_registry().requirements())`; keep the
  assertions (`any("Contract__c" in m ...)`, `any("AccountId" in m ...)`).
- ADD `test_coverage_reports_closedate_dependent_checks`: grader with requirements, object
  "Opportunity", fields `["Id","Name","StageName","Amount","AccountId","Contract__c","Segment__c"]`
  (no CloseDate), non-empty records -> `any("CloseDate" in m for m in suppressed_checks)` is True and
  `any("Stale-open" in m for m in suppressed_checks)` is True.
- Keep the blocked + profiler tests as-is.

## Constraints
- Grade/missing/reason parity preserved; only `suppressed_checks` enriched.
- coverage imports only `app.domain.*` + `app.policy.*` (NOT detection). detection imports domain.
- `from __future__ import annotations`; type hints; docstrings.
- Do NOT modify `tests/test_scanner.py` or `tests/test_detection.py`.

## Done criteria
- `python -m pytest -q` green. Report files changed + results.
