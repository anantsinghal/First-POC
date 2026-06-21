# Phase 3 — Profiling + Coverage modules

Extract `profile_records()` and `calculate_coverage()` from `app/main.py` into dedicated classes,
producing the domain `Profile` and `Coverage` value objects from Phase 1. **Output must match the
baseline JSON exactly.** Do NOT modify `app/main.py`, `app/salesforce.py`, or `tests/test_scanner.py`.

## `app/profiling/__init__.py`, `app/coverage/__init__.py`
Empty package markers.

## `app/profiling/profiler.py` — `class Profiler`
Method `def profile(self, records: list[Record], fields: list[str]) -> Profile`, mirroring baseline
`profile_records` exactly:
- For each field: `values = [clean(row.get(field)) for row in records]`; `non_empty = [v for v in
  values if v]`.
- `null_rate = round(1 - len(non_empty)/len(records), 3) if records else 0`
- `distinct_count = len(set(non_empty))`
- `top_values = Counter(non_empty).most_common(5)`  (list of (value, count) tuples)
- `numbers = numeric_values(records, field)`; if `len(numbers) >= 2`, set `numeric = {"min": min,
  "max": max, "mean": round(mean,2), "median": round(median,2)}` (use `statistics.mean/median`),
  else `numeric=None`.
- Build `FieldProfile(null_rate, distinct_count, top_values, numeric)` per field and return
  `Profile(record_count=len(records), fields={field: FieldProfile...})`.
- `Profile.to_dict()` must equal baseline `profile_records` output (numeric key omitted when None).

## `app/coverage/grader.py` — `class CoverageGrader`
`__init__(self, policy: FieldPolicy)`. Method `def grade(self, object_name: str, records:
list[Record], fields: list[str]) -> Coverage`, mirroring baseline `calculate_coverage`:
- `expected = self.policy.expected_fields(object_name)`; `missing = sorted(expected - set(fields))`.
- `suppressed: list[str] = []`; if `"Id" in missing`: append `"all record-level checks: Id is absent"`.
- If `object_name.lower() == "opportunity"`:
  - if `"StageName" in missing`: append `"invalid-stage and closed-won rules: StageName is absent"`
  - if `"Contract__c" in missing`: append `"missing-contract rule: Contract__c is absent"`
  - if `"AccountId" in missing`: append `"missing-account rule: AccountId is absent"`
- Grade/reason:
  - `not records or "Id" in missing` -> `CoverageGrade.BLOCKED`, reason `"No trustworthy record-level
    scan: input is empty or has no Id column."`
  - elif `missing` -> `CoverageGrade.PARTIAL`, reason `"Some expected fields are unavailable;
    dependent checks were suppressed."`
  - else -> `CoverageGrade.GOOD`, reason `"Selected POC fields were present in the supplied file."`
- Return `Coverage(object_name=object_name, observed_records=len(records), queried_fields=fields,
  missing_fields=missing, suppressed_checks=suppressed, grade=..., reason=...)`.

## Tests — `tests/test_profiling_coverage.py` (NEW, value-based so it survives the later main.py rewrite)
Load the 11-row demo via csv.DictReader + `clean`. Assert:
- `CoverageGrader(DEFAULT_POLICY).grade("Opportunity", records, fields)` -> grade GOOD,
  `missing_fields == []`, `suppressed_checks == []`, `observed_records == 11`.
- A partial case: fields `["Id","Name","StageName"]` (records with those) -> grade PARTIAL and the
  suppressed list contains the Contract__c and AccountId messages.
- A blocked case: empty records -> grade BLOCKED.
- `Profiler().profile(records, fields).to_dict()` has `record_count == 11`, and the `Amount` field's
  `numeric` dict has `min == -5000.0` and `max == 2500000.0`.

## Constraints
- `from __future__ import annotations`; full type hints; one-line docstrings.
- profiling/coverage import only from `app.domain.*` and `app.policy.*`.
- Don't touch existing files except adding the new test file.

## Done criteria
- `python -m pytest -q` green. Report files + results.
