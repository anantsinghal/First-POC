# Phase 18 — Rule catalog + interpreter (business logic as DATA, not code)

Retire the hardcoded *conditional* Opportunity detectors and replace them with ONE generic rule
interpreter that executes declarative rules loaded from a data catalog (blueprint doc 04/10 rule
representation). This proves "business logic is data." Behavior, finding types, and demo counts must
stay IDENTICAL (the rules reproduce the retired detectors exactly), so existing tests stay green.

Detectors being RETIRED in this phase (their logic becomes catalog rows):
`ClosedWonContractDetector` (missing_contract_closed_won), `NonPositiveAmountDetector`
(non_positive_amount), `StaleOpenOpportunityDetector` (stale_open_opportunity),
`MissingCloseDateDetector` (missing_close_date).
KEPT for now (schema-role ones, handled in a later phase): `InvalidStageDetector`,
`MissingAccountDetector`, `RequiredFieldsDetector`, plus all statistical/pattern/ML/schema-driven
detectors and `DuplicateNameDetector`.

## 1. Rule model + loader — `app/policy/rules.py`
- `@dataclass(frozen=True) class Condition`: `field: str`, `op: str`, `value: Any = None`.
- `@dataclass(frozen=True) class Rule`: `rule_id: str`, `object_name: str`, `finding_type: str`,
  `title: str`, `when: tuple[Condition, ...]`, `expect: Condition | None`, `explanation: str`,
  `field_name: str | None = None`. Add `referenced_fields() -> set[str]` = fields used in `when` +
  `expect`.
- `def load_rule_catalog(directory: Path | None = None) -> list[Rule]`: read every `*.json` in
  `app/policy/rules/` (default), each file a list of rule dicts; parse into `Rule`s. (Tolerate missing
  dir → return [].)
- Operators supported (document in a docstring):
  - `when`/`expect` comparison ops via a helper `evaluate_condition(record, cond, today) -> bool`:
    `equals`, `not_equals`, `in` (value is list), `not_in`, `blank`, `not_blank`,
    `lt`/`lte`/`gt`/`gte` (numeric via `parse_float`; False if unparseable),
    `before`/`after` (dates via `parse_date`; `value=="today"` resolves to the injected today; else
    parse the literal). `clean()` the record field first.

## 2. Seed catalog — `app/policy/rules/opportunity.json`
Exactly four rules reproducing the retired detectors (same finding_type, title, explanation, fields):
- `{"rule_id":"opp_closed_won_requires_contract","object_name":"Opportunity",
  "finding_type":"missing_contract_closed_won","title":"Closed Won Opportunity has no contract",
  "field_name":"Contract__c","when":[{"field":"StageName","op":"equals","value":"Closed Won"}],
  "expect":{"field":"Contract__c","op":"not_blank"},
  "explanation":"POC policy expects Contract__c for Closed Won opportunities."}`
- `{"rule_id":"opp_non_positive_amount","object_name":"Opportunity","finding_type":"non_positive_amount",
  "title":"Opportunity Amount is zero or negative","field_name":"Amount",
  "when":[{"field":"Amount","op":"lte","value":0}],"expect":null,
  "explanation":"POC policy treats non-positive opportunity value as reviewable."}`
- `{"rule_id":"opp_stale_open","object_name":"Opportunity","finding_type":"stale_open_opportunity",
  "title":"Open Opportunity past its Close Date","field_name":"CloseDate",
  "when":[{"field":"StageName","op":"not_in","value":["Closed Won","Closed Lost"]},
  {"field":"CloseDate","op":"before","value":"today"}],"expect":null,
  "explanation":"Opportunity is still open but its CloseDate is in the past; the forecast or stage likely needs updating."}`
- `{"rule_id":"opp_missing_close_date","object_name":"Opportunity","finding_type":"missing_close_date",
  "title":"Opportunity has no Close Date","field_name":"CloseDate","when":[],
  "expect":{"field":"CloseDate","op":"not_blank"},
  "explanation":"Opportunity has no CloseDate; forecasting and stage SLAs cannot be evaluated."}`

## 3. Interpreter — `app/detection/rules/interpreter.py` — `class RuleInterpreterDetector`
- `id: str = "rule_interpreter"`; `__init__(self, rules: list[Rule], *, today: date | None = None)`.
- `def _today(self)` → injected today or `date.today()` (lazy, never at import).
- `detect(ctx)`: for each rule where `rule.object_name.lower() == ctx.object_name.lower()`:
  - if not all of `rule.referenced_fields()` are in `ctx.fields` → skip the rule (coverage suppression).
  - for each record with non-empty Id: a record VIOLATES the rule when **all** `when` conditions are
    true AND (`expect is None` OR `not evaluate_condition(record, expect, today)`). For each violator,
    emit `make_candidate(ctx, record, rule=rule.finding_type, title=rule.title,
    field_name=rule.field_name, evidence={"method":"rule catalog","rule_id":rule.rule_id,
    "matched":{c.field: clean(record.get(c.field)) for c in rule.when},
    "observed_value": clean(record.get(rule.field_name)) or None if rule.field_name else None,
    "why": rule.explanation})`. (Severity/confidence/priority come from `policy.severity_for(finding_type)`
    — the existing rows already exist for these four finding types.)
- `def coverage_requirements(self) -> list[CheckRequirement]`: one per rule scoped to its object —
  `CheckRequirement(label=f"{rule.title} (rule)", requires=tuple(sorted(rule.referenced_fields())),
  object_scope=rule.object_name.lower())`. (So the grader still reports e.g. "needs Contract__c"
  when fields are absent — preserves coverage messaging.)

## 4. Registry — `app/detection/registry.py`
- Remove imports/registration of the 4 retired detectors.
- `from ..policy.rules import load_rule_catalog` and `from .rules.interpreter import RuleInterpreterDetector`;
  append `RuleInterpreterDetector(load_rule_catalog())` to `build_default_registry()`.
- Extend `requirements()` to also gather from detectors exposing a callable `coverage_requirements()`:
  ```
  reqs = []
  for d in self._detectors:
      cr = getattr(d, "coverage_requirements", None)
      if callable(cr): reqs.extend(cr())
      elif getattr(d, "requirement", None) is not None: reqs.append(d.requirement)
  return reqs
  ```

## 5. Retire the detector classes
- DELETE `app/detection/rules/temporal.py` and `app/detection/rules/completeness.py`.
- EDIT `app/detection/rules/opportunity.py`: remove `ClosedWonContractDetector` and
  `NonPositiveAmountDetector` (keep `InvalidStageDetector`, `MissingAccountDetector`).

## 6. Tests
- NEW `tests/test_rule_interpreter.py`: load the seed catalog; build a synthetic Opportunity context
  and assert each of the 4 finding types fires on the right records (use an injected `today` for the
  stale-open case); assert a rule is SKIPPED (no findings) when a referenced field is absent from
  `ctx.fields`; assert `coverage_requirements()` includes a Contract__c-referencing requirement.
- UPDATE `tests/test_new_detectors.py`: remove `test_stale_open_opportunity_detector` and
  `test_missing_close_date_detector` (their classes are retired); KEEP `test_duplicate_name_detector`
  and the demo regression guard (demo still 10; the three types still 0 on the demo, now via rules).
- Do NOT modify `tests/test_scanner.py`, `tests/test_detection.py`, `tests/test_profiling_coverage.py`,
  `tests/test_pattern_miner.py`, `tests/test_ml_anomaly.py`, `tests/test_schema_driven.py`,
  `tests/test_persistence.py`, `tests/test_ingestion.py`.

## Constraints
- Demo (11-row) finding count and type distribution must stay EXACTLY the same (10 total; the four
  migrated types behave identically). Coverage suppressed-checks must still mention Contract__c.
- `from __future__ import annotations`; type hints; docstrings.

## Done criteria
- `python -m pytest -q` green. Report files created/changed/deleted, the demo finding count + type
  counts (must match the pre-phase distribution), and confirm coverage still reports Contract__c when absent.
