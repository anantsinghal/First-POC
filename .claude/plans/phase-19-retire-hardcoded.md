# Phase 19 — Retire the last hardcoded detectors into the rule catalog

Move the final three Opportunity-specific detectors into DATA rules so NOTHING in the detection code
hardcodes object/field names or value lists. Finding types and demo behavior are preserved EXACTLY
(the rules reproduce the detectors), so almost all tests stay green untouched.

Detectors RETIRED this phase: `InvalidStageDetector` (invalid_stage), `MissingAccountDetector`
(missing_account), `RequiredFieldsDetector` (missing_required). After this, the only object-specific
knowledge lives in DATA (`app/policy/rules/opportunity.json`) and the metadata catalog — never in code.
(Note: live Salesforce orgs get picklist/required/reference checks from describe via the Phase-17
schema-driven engines; the rule below carries the demo's stage list as data because the CSV demo has
no live metadata.)

## 1. Add three rules to `app/policy/rules/opportunity.json`
Append (keep the existing four rules). Reproduce the retired detectors exactly:

- invalid_stage (only when StageName is present and not in the allowed set):
```json
{"rule_id":"opp_invalid_stage","object_name":"Opportunity","finding_type":"invalid_stage",
 "title":"Unknown Opportunity stage","field_name":"StageName",
 "when":[{"field":"StageName","op":"not_blank"}],
 "expect":{"field":"StageName","op":"in","value":["Prospecting","Qualification","Needs Analysis",
   "Value Proposition","Id. Decision Makers","Perception Analysis","Proposal/Price Quote",
   "Negotiation/Review","Closed Won","Closed Lost","Proposal"]},
 "explanation":"StageName is not in the org's allowed stage values."}
```
- missing_account (Opportunity should have a parent Account):
```json
{"rule_id":"opp_missing_account","object_name":"Opportunity","finding_type":"missing_account",
 "title":"Opportunity has no Account","field_name":"AccountId","when":[],
 "expect":{"field":"AccountId","op":"not_blank"},
 "explanation":"The record has no parent AccountId in the extracted data."}
```
- missing_required (Name required):
```json
{"rule_id":"opp_required_name","object_name":"Opportunity","finding_type":"missing_required",
 "title":"Missing required Name","field_name":"Name","when":[],
 "expect":{"field":"Name","op":"not_blank"},
 "explanation":"Name is empty."}
```

(`evaluate_condition` already supports `not_blank` and `in`; no interpreter change needed. The
`in` value list IS the stage allowlist as data.)

## 2. Retire the detector classes
- EDIT `app/detection/rules/opportunity.py`: remove `InvalidStageDetector` and `MissingAccountDetector`.
  If the file is then empty of classes, DELETE it.
- DELETE `app/detection/rules/required_fields.py` (`RequiredFieldsDetector`).
- `app/detection/registry.py`: remove the imports + registrations of all three. (The
  `RuleInterpreterDetector(load_rule_catalog())` already registered in Phase 18 now covers them.)

## 3. Remove the now-unused hardcoded stage list from policy
- `app/policy/field_policy.py`: remove `VALID_OPPORTUNITY_STAGES` and the `valid_stages()` method (the
  allowlist now lives in the rule data). Keep `expected_fields()` and `severity_for()` unchanged
  (severity_for already has rows for invalid_stage / missing_account / missing_required). First confirm
  nothing else references `valid_stages` / `VALID_OPPORTUNITY_STAGES`.

## 4. Tests
- `tests/test_detection.py`: it currently has a unit test that imports/uses `RequiredFieldsDetector`
  (asserting it returns [] when "Name" absent). REMOVE that test and its import. KEEP the
  `build_default_registry().detect(...)` assertions that `invalid_stage`, `missing_account`,
  `missing_contract_closed_won`, `non_positive_amount` appear on the demo — they still pass (now via
  the rule interpreter, no schema needed). If it imports `InvalidStageDetector`/`MissingAccountDetector`
  directly anywhere, update those references too.
- `tests/test_rule_interpreter.py`: ADD cases asserting the three new rules fire correctly — an
  out-of-allowlist StageName → invalid_stage; an empty AccountId → missing_account; an empty Name →
  missing_required; and that a valid StageName / present AccountId / present Name do NOT fire.
- Do NOT modify `tests/test_scanner.py`, `tests/test_new_detectors.py`, `tests/test_pattern_miner.py`,
  `tests/test_ml_anomaly.py`, `tests/test_profiling_coverage.py`, `tests/test_schema_driven.py`,
  `tests/test_persistence.py`, `tests/test_ingestion.py` — they stay green because finding types and
  the demo distribution are unchanged.

## Constraints
- Demo (11-row) finding count stays 10 with the SAME type distribution
  (invalid_stage 1, missing_account 1, missing_required 0, missing_contract_closed_won 1,
  non_positive_amount 1, numeric_outlier 1, robust_outlier 1, rare_combination 4).
- Coverage still reports StageName / AccountId / Contract__c in suppressed checks when those fields
  are absent (the rules' `coverage_requirements()` provide them).
- `from __future__ import annotations`; type hints; docstrings.

## Done criteria
- `python -m pytest -q` green. Report files changed/deleted, the demo finding count + per-type counts
  (must match the distribution above), and confirm no code under `app/detection/` hardcodes a
  Salesforce object name or field allowlist anymore (only `app/policy/` data + coverage `expected_fields`).
