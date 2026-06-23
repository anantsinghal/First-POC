# Phase 22 — Integration hardening test (lock in cross-detector correctness)

We have 14 detector families. This phase codifies a permanent integration test that exercises the
WHOLE registry together and asserts hard invariants, so future changes can't silently break
cross-detector behavior. Pure test addition — no app code changes.

Do NOT modify any app code or any existing test file. Add ONE new file: `tests/test_integration.py`.

## `tests/test_integration.py`

Use `schema_from_dict`, `DetectionContext`, `build_default_registry`, `DEFAULT_POLICY`, and the real
demo CSV. Two tests:

### 1) `test_full_registry_rich_schema_integration`
Build this EXACT fixture (deterministic — no randomness):
- schema via `schema_from_dict({"object_name":"Opportunity","fields":[...]})` with fields:
  Id(id), Name(string, required, is_name_field), StageName(picklist,
  picklist_values=["Prospecting","Qualification","Closed Won","Closed Lost"]),
  AccountId(reference, required, reference_to=["Account"]), OwnerEmail(email), Website(url),
  Amount(number), Contract__c(reference, reference_to=["Contract"]),
  Segment__c(picklist, picklist_values=["Enterprise","SMB"]), CloseDate(date).
- `valid_accts = {f"001ACC{i:03d}" for i in range(60)}`.
- 60 records, record i:
  `Id=f"006OPP{i:03d}"`, `Name=f"Deal {i}"`, `StageName="Closed Won" if i%2==0 else "Prospecting"`,
  `AccountId=f"001ACC{i:03d}"`, `OwnerEmail=f"owner{i}@example.com"`, `Website="https://example.com"`,
  `Amount=str(100000 + (i%10)*1000)`, `Contract__c=f"800CON{i:03d}" if i%2==0 else ""`,
  `Segment__c="Enterprise" if i%3 else "SMB"`, `CloseDate="2026-12-01"`.
- Inject anomalies: recs[5].StageName="Frozen"; recs[6].Name=""; recs[7].OwnerEmail="not-an-email";
  recs[8].Website="nope"; recs[9].AccountId="001MISSING"; recs[10].Amount="9000000";
  recs[11].AccountId=""; recs[12].Contract__c="".
- `ctx = DetectionContext("Opportunity", recs, fields, DEFAULT_POLICY, schema=schema,
  related_ids={"Account": frozenset(valid_accts)})`.
- `reg = build_default_registry()`.
- Assertions:
  - For each detector in `reg.detectors`, calling `d.detect(ctx)` raises NO exception (loop in a
    try/except, fail with the detector class name + error if any).
  - `findings = reg.detect(ctx)`. The set of `finding_type`s present is a SUPERSET of:
    {"numeric_outlier","robust_outlier","rare_combination","pattern_break","ml_anomaly",
    "required_missing","picklist_invalid","reference_missing","orphan_reference","invalid_format",
    "missing_contract_closed_won"}.
  - All fingerprints unique: `len({f.fingerprint for f in findings}) == len(findings)`.
  - Every finding: `severity.value in {"high","medium","low"}`; `0.0 <= confidence <= 1.0`;
    `0.0 <= priority <= 1.0`; `record_id` non-empty; `isinstance(evidence, dict)` with `"why"` in it;
    `json.dumps(evidence)` succeeds.

### 2) `test_demo_offline_contract`
Load the real 11-row `data/demo_opportunities.csv` (csv.DictReader + clean), build a
`DetectionContext` WITHOUT a schema, run `build_default_registry().detect(ctx)`. Assert:
- exactly 10 findings;
- per-type counts exactly: numeric_outlier=1, robust_outlier=1, rare_combination=4,
  missing_contract_closed_won=1, non_positive_amount=1, invalid_stage=1, missing_account=1;
- the schema-driven types are ABSENT (no-op without schema): none of
  {"required_missing","picklist_invalid","reference_missing","invalid_format","orphan_reference"}
  appear, and "pattern_break"/"ml_anomaly" are absent (below their min-record thresholds on 11 rows).

## Constraints
- `from __future__ import annotations`; type hints where natural; clear assert messages.
- New file only. No app or existing-test changes.

## Done criteria
- `python -m pytest -q` green (existing + the 2 new integration tests). Report results.
