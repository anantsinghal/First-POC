# Phase 1 — Domain + Policy layer

Pure foundation. No FastAPI, no httpx, no sqlite imports anywhere in these files. Additive only —
do NOT modify `app/main.py`, `app/salesforce.py`, or `tests/` in this phase.

## Files to create

### `app/__init__.py` (if missing) and package `__init__.py` files
Create empty `app/domain/__init__.py` and `app/policy/__init__.py`.

### `app/domain/enums.py`
`str`-based enums (so `json.dumps` emits the plain string, preserving API shape):
- `Severity(str, Enum)`: HIGH="high", MEDIUM="medium", LOW="low"
- `CoverageGrade(str, Enum)`: GOOD="good", PARTIAL="partial", BLOCKED="blocked"
- `FindingStatus(str, Enum)`: OPEN="open", REOPENED="reopened", RESOLVED="resolved",
  ACKNOWLEDGED="acknowledged", SUPPRESSED_UNTIL="suppressed_until"
- `FeedbackLabel(str, Enum)`: REAL_ISSUE="real_issue", FALSE_POSITIVE="false_positive",
  VALID_EXCEPTION="valid_exception", NEEDS_REVIEW="needs_review"

### `app/domain/models.py`
Framework-free dataclasses + helpers:
- `Record = dict[str, str]` (type alias)
- `def stable_hash(value: Any) -> str` — `sha256` of `json.dumps(value, sort_keys=True,
  default=str, separators=(",", ":"))`. (Same algorithm the baseline uses for fingerprints.)
- `@dataclass(frozen=True) class Candidate` with fields: `object_name: str`, `record_id: str`,
  `record_name: str`, `finding_type: str`, `field_name: str | None`, `severity: Severity`,
  `confidence: float`, `priority: float`, `title: str`, `evidence: dict[str, Any]`.
  Add `@property fingerprint(self) -> str` returning
  `stable_hash({"object": object_name, "record": record_id, "rule": finding_type, "field": field_name})`.
- `@dataclass(frozen=True) class FieldProfile`: `null_rate: float`, `distinct_count: int`,
  `top_values: list[tuple[str, int]]`, `numeric: dict[str, float] | None = None`.
- `@dataclass(frozen=True) class Profile`: `record_count: int`, `fields: dict[str, FieldProfile]`.
  Add `def to_dict(self) -> dict[str, Any]` producing exactly:
  `{"record_count": n, "fields": {name: {"null_rate":..., "distinct_count":..., "top_values":...,
  ["numeric":...]}}}` — omit the `numeric` key when None (matches baseline JSON shape).
- `@dataclass(frozen=True) class Coverage`: `object_name: str`, `observed_records: int`,
  `queried_fields: list[str]`, `missing_fields: list[str]`, `suppressed_checks: list[str]`,
  `grade: CoverageGrade`, `reason: str`. Add `def to_dict(self) -> dict[str, Any]` with keys
  exactly: object_name, observed_records, queried_fields, missing_fields, suppressed_checks,
  grade (the .value string), reason.

### `app/policy/field_policy.py`
- Module constant `VALID_OPPORTUNITY_STAGES: frozenset[str]` = the same 11 stages in
  `app/main.py` (Prospecting, Qualification, Needs Analysis, Value Proposition, Id. Decision Makers,
  Perception Analysis, Proposal/Price Quote, Negotiation/Review, Closed Won, Closed Lost, Proposal).
- `@dataclass(frozen=True) class FieldPolicy` encapsulating policy with methods:
  - `expected_fields(self, object_name: str) -> set[str]` — `{"Id"}`, plus for object_name.lower()
    == "opportunity" also `{"Name","StageName","Amount","AccountId","Contract__c"}`.
  - `valid_stages(self) -> frozenset[str]` — returns VALID_OPPORTUNITY_STAGES.
  - `severity_for(self, rule: str) -> tuple[Severity, float, float]` — the exact table from
    `severity_for` in `app/main.py` (missing_required high/0.95/0.90, invalid_stage high/0.98/0.95,
    missing_account high/0.95/0.90, missing_contract_closed_won high/0.92/0.86,
    non_positive_amount medium/0.92/0.75, numeric_outlier medium/0.76/0.57,
    rare_combination low/0.68/0.36).
- Module constant `DEFAULT_POLICY = FieldPolicy()` for the composition root to import.

## Constraints
- Type hints + a one-line docstring on every class/function.
- `from __future__ import annotations` at top of each module.
- No imports from `fastapi`, `httpx`, `sqlite3`, or `app.main`.
- Do not modify any existing file.

## Done criteria
- `python -c "import app.domain.models, app.domain.enums, app.policy.field_policy"` succeeds.
- Report what was created. Do NOT run or modify tests.
