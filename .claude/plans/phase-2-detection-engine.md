# Phase 2 — Detection engine (Strategy pattern)

Port the baseline's 7 checks (in `app/main.py` `analyze()` / `create_candidate` / `severity_for`)
into discrete **Detector** strategies behind a registry. **Behavior must be identical** — same
titles, field_name, evidence dicts, severity/confidence/priority, and fingerprints — so the existing
tests and the UI keep working unchanged.

Do NOT modify `app/main.py`, `app/salesforce.py`, or `tests/test_scanner.py`. You MAY add a new
test file `tests/test_detection.py`.

## Shared value helpers — `app/domain/values.py`
Pure functions (no I/O):
- `def clean(value: str | None) -> str` -> `(value or "").strip()`
- `def display_name(record: dict[str, str]) -> str` -> `clean(record.get("Name")) or clean(record.get("Id"))`
- `def parse_float(raw: str | None) -> float | None` -> `float(clean(raw).replace(",", ""))` else None on ValueError
- `def numeric_values(records: list[dict[str, str]], field: str) -> list[float]` -> list of
  `parse_float` over each record's field, skipping None.

## `app/detection/__init__.py`, `app/detection/rules/__init__.py`, `app/detection/statistical/__init__.py`
Empty package markers.

## `app/detection/base.py`
- `@dataclass(frozen=True) class DetectionContext`: `object_name: str`, `records: list[Record]`,
  `fields: list[str]`, `policy: FieldPolicy`, `profile: Profile | None = None`.
  - `@property field_set(self) -> set[str]` -> `set(self.fields)`
  - `def has(self, name: str) -> bool` -> `name in self.field_set`
  - `def is_opportunity(self) -> bool` -> `self.object_name.lower() == "opportunity"`
- `@runtime_checkable class Detector(Protocol)`: class attr `id: str`; method
  `def detect(self, ctx: DetectionContext) -> list[Candidate]: ...`
- `def make_candidate(ctx, record, rule, title, field_name, evidence) -> Candidate`:
  looks up `severity, confidence, priority = ctx.policy.severity_for(rule)` and returns a
  `Candidate(object_name=ctx.object_name, record_id=clean(record.get("Id")),
  record_name=display_name(record), finding_type=rule, field_name=field_name, severity=severity,
  confidence=confidence, priority=priority, title=title, evidence=evidence)`.
  NOTE: `Candidate` has no id/status — those are assigned later by persistence.

## Rule detectors — `app/detection/rules/`
Each is a small class with a class attribute `id` and a `detect(ctx)` method. Per-record detectors
must skip records where `clean(record.get("Id"))` is empty (baseline does `if not Id: continue`).

`required_fields.py` — `class RequiredFieldsDetector` (`id="missing_required"`):
  optional `__init__(self, required: tuple[str, ...] = ("Name",))`. For each record with Id, for each
  `name` in `required` where `ctx.has(name)` and `clean(record.get(name))` is empty, emit candidate:
  title `f"Missing required {name}"`, field_name `name`,
  evidence `{"rule": "POC required-field check", "observed_value": None, "why": f"{name} is empty."}`.

`opportunity.py` — four separate detectors, each returns `[]` unless `ctx.is_opportunity()`:
- `class InvalidStageDetector` (`id="invalid_stage"`): if `ctx.has("StageName")`, for each record with
  Id where `stage = clean(record.get("StageName"))` is truthy and `stage not in ctx.policy.valid_stages()`:
  title "Unknown Opportunity stage", field_name "StageName",
  evidence `{"observed_value": stage, "allowed_values": sorted(ctx.policy.valid_stages()), "why":
  "StageName is not in the POC stage allowlist."}`.
- `class MissingAccountDetector` (`id="missing_account"`): if `ctx.has("AccountId")`, for each record
  with Id where `clean(record.get("AccountId"))` is empty: title "Opportunity has no Account",
  field_name "AccountId", evidence `{"observed_value": None, "why": "The record has no parent
  AccountId in the extracted data."}`.
- `class ClosedWonContractDetector` (`id="missing_contract_closed_won"`): if `ctx.has("StageName")`
  and `ctx.has("Contract__c")`, for each record with Id where `clean(StageName) == "Closed Won"` and
  `clean(Contract__c)` empty: title "Closed Won Opportunity has no contract", field_name
  "Contract__c", evidence `{"stage": stage, "observed_value": None, "why": "POC policy expects
  Contract__c for Closed Won opportunities."}`.
- `class NonPositiveAmountDetector` (`id="non_positive_amount"`): if `ctx.has("Amount")`, for each
  record with Id where `parse_float(record.get("Amount"))` is not None and `<= 0`: title
  "Opportunity Amount is zero or negative", field_name "Amount",
  evidence `{"observed_value": record.get("Amount"), "why": "POC policy treats non-positive
  opportunity value as reviewable."}`.

## Statistical detectors — `app/detection/statistical/`
`iqr_outlier.py` — `class IqrOutlierDetector` (`id="numeric_outlier"`): for each field in `ctx.fields`,
  `values = sorted(numeric_values(ctx.records, field))`; if `len(values) < 8` skip;
  `q1, q3 = values[len(values)//4], values[(len(values)*3)//4]`; `iqr = q3 - q1`; if `iqr <= 0` skip;
  `lower, upper = q1 - 1.5*iqr, q3 + 1.5*iqr`; for each record, `v = parse_float(record.get(field))`;
  skip None; if `v < lower or v > upper`: title `f"Unusual {field} value"`, field_name `field`,
  evidence `{"observed_value": v, "method": "IQR", "lower_bound": round(lower,2), "upper_bound":
  round(upper,2), "why": "The value falls outside the 1.5×IQR range of this scan."}`.
  (Keep the exact index-based quartiles above — do NOT use statistics.quantiles — for parity.)

`rare_combination.py` — `class RareCombinationDetector` (`id="rare_combination"`):
  `category_fields = [f for f in ("StageName", "Segment__c") if ctx.has(f)]`; only if
  `len(category_fields) == 2 and len(ctx.records) >= 8`. Build
  `combos = Counter(tuple(clean(r.get(f)) for f in category_fields) for r in ctx.records)`. For each
  record, `combo = tuple(clean(r.get(f)) for f in category_fields)`; if `all(combo)` and
  `combos[combo] == 1`: title "Rare stage and segment combination",
  field_name `", ".join(category_fields)`, evidence `{"combination": dict(zip(category_fields,
  combo)), "occurrences": 1, "population": len(ctx.records), "why": "This combination occurs once in
  the supplied scan. Human review is required."}`.

## Registry — `app/detection/registry.py`
- `class DetectorRegistry`: `__init__(self, detectors: Iterable[Detector] = ())`; `register(self,
  detector: Detector) -> "DetectorRegistry"` (appends, returns self); `@property detectors ->
  tuple[Detector, ...]`; `def detect(self, ctx: DetectionContext) -> list[Candidate]` runs every
  detector in registration order and concatenates results.
- `def build_default_registry() -> DetectorRegistry` registering, IN THIS ORDER:
  RequiredFieldsDetector, InvalidStageDetector, MissingAccountDetector, ClosedWonContractDetector,
  NonPositiveAmountDetector, IqrOutlierDetector, RareCombinationDetector.

## Tests — `tests/test_detection.py` (NEW file)
Add focused unit tests (no FastAPI): build a `DetectionContext` from `DEFAULT_POLICY` and the 11-row
demo dataset (read `data/demo_opportunities.csv` with csv.DictReader, clean values) and assert:
- `build_default_registry().detect(ctx)` yields at least one candidate of each finding_type that the
  demo triggers: `invalid_stage`, `missing_account`, `missing_contract_closed_won`,
  `non_positive_amount`.
- A blocked/empty field set: RequiredFieldsDetector returns [] when "Name" not in fields.
- Fingerprint stability: same record+rule+field across two runs gives equal `.fingerprint`.

## Constraints
- `from __future__ import annotations`; full type hints; one-line docstrings.
- Detection layer imports only from `app.domain.*` and `app.policy.*` (never fastapi/httpx/sqlite/main).
- Don't touch existing files except adding the new test file.

## Done criteria
- `python -m pytest -q` is green (old + new tests).
- Report files created and test output.
