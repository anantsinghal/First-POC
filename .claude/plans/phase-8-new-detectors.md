# Phase 8 — New value-add detectors (Open/Closed Principle in action)

Add three NEW anomaly families by writing new Strategy classes + registering them + adding their
severity entries to policy. The detection engine, services, persistence, and API must NOT change
(that's the whole point of the Strategy design). These three are chosen to NOT fire on the curated
11-row demo (so `tests/test_scanner.py` and the existing demo behavior are unchanged) but to catch
real Salesforce hygiene problems on other data.

New families: (1) temporal cross-field consistency, (2) duplication, (3) completeness.

Do NOT modify `tests/test_scanner.py`, `tests/test_detection.py`, or any layer above detection/policy.

## Policy additions — `app/policy/field_policy.py`
Add to the `severity_for` table (keep existing 7 untouched):
- `"stale_open_opportunity": (Severity.HIGH, 0.90, 0.88)`
- `"duplicate_name": (Severity.MEDIUM, 0.80, 0.65)`
- `"missing_close_date": (Severity.MEDIUM, 0.85, 0.60)`

## Helper — add to `app/domain/values.py`
- `def parse_date(raw: str | None) -> date | None`: parse `clean(raw)` as `"%Y-%m-%d"` via
  `datetime.strptime(...).date()`; return None on empty/ValueError. (Import `date`, `datetime`.)

## New detectors

`app/detection/statistical/` is for numeric/statistical; these new ones are rule-ish + temporal, so
put them under `app/detection/rules/`:

`app/detection/rules/temporal.py` — `class StaleOpenOpportunityDetector` (`id="stale_open_opportunity"`):
- Class attr `CLOSED_STAGES = frozenset({"Closed Won", "Closed Lost"})`.
- `__init__(self, today: date | None = None)` — store; `def _today(self) -> date` returns
  `self._today_value or date.today()` (call `date.today()` lazily inside detect, NOT at import).
- `detect(ctx)`: return [] unless `ctx.is_opportunity()` and `ctx.has("CloseDate")` and
  `ctx.has("StageName")`. For each record with Id: `stage = clean(StageName)`; skip if `stage in
  CLOSED_STAGES`; `parsed = parse_date(record.get("CloseDate"))`; skip if None; if `parsed < today`
  emit candidate: title "Open Opportunity past its Close Date", field_name "CloseDate",
  evidence `{"observed_value": clean(record.get("CloseDate")), "stage": stage, "as_of":
  today.isoformat(), "why": "Opportunity is still open but its CloseDate is in the past; the
  forecast or stage likely needs updating."}`.

`app/detection/rules/duplicates.py` — `class DuplicateNameDetector` (`id="duplicate_name"`):
- `detect(ctx)`: return [] unless `ctx.has("Name")`. Build a `Counter` of `clean(Name).casefold()`
  over records that have a non-empty Id AND non-empty Name. `dups = {k for k,v in counter.items() if
  v > 1}`; if empty return []. For each record with Id and non-empty Name whose normalized name is in
  `dups`: emit candidate: title "Possible duplicate by Name", field_name "Name",
  evidence `{"observed_value": clean(record.get("Name")), "duplicate_count": counter[norm], "why":
  "Another record in this scan shares this Name; verify these are not duplicates."}`.

`app/detection/rules/completeness.py` — `class MissingCloseDateDetector` (`id="missing_close_date"`):
- `detect(ctx)`: return [] unless `ctx.is_opportunity()` and `ctx.has("CloseDate")`. For each record
  with Id where `clean(record.get("CloseDate"))` is empty: emit candidate: title "Opportunity has no
  Close Date", field_name "CloseDate", evidence `{"observed_value": None, "why": "Opportunity has no
  CloseDate; forecasting and stage SLAs cannot be evaluated."}`.

All three use `make_candidate(ctx, record, rule, title, field_name, evidence)` from
`app/detection/base.py` and import `clean`/`parse_date` from `app.domain.values`.

## Registry — `app/detection/registry.py`
Extend `build_default_registry()` to APPEND (after the existing 7, preserving their order):
`StaleOpenOpportunityDetector()`, `DuplicateNameDetector()`, `MissingCloseDateDetector()`.

## Tests — `tests/test_new_detectors.py` (NEW)
Use `DEFAULT_POLICY` and craft small `DetectionContext`s:
- StaleOpen: two Opportunity records, one Prospecting with CloseDate "2020-01-01", one Closed Won with
  "2020-01-01"; `StaleOpenOpportunityDetector(today=date(2026,1,1)).detect(ctx)` -> exactly 1
  candidate (the open one), finding_type "stale_open_opportunity", and evidence `as_of` == "2026-01-01".
- Duplicate: three records Names ["Acme","acme","Beta"] (all with Ids) ->
  `DuplicateNameDetector().detect(ctx)` -> 2 candidates (the two Acme variants), duplicate_count == 2.
- MissingCloseDate: Opportunity records, one with CloseDate "", one with a value ->
  `MissingCloseDateDetector().detect(ctx)` -> exactly 1 candidate.
- Regression guard: loading the 11-row demo into a context and running
  `build_default_registry().detect(ctx)` still yields exactly the 9 baseline candidates when today is
  pinned to date(2026,6,21) (the new detectors fire 0 on the demo) — assert no finding_type in
  {"stale_open_opportunity","duplicate_name","missing_close_date"} appears.
  (Build a registry with the pinned-today detector for this assertion, OR assert the three new
  types are absent from the default-registry output given all demo CloseDates are in the future as of
  2026-06-21 and all Names are unique.)

## Constraints
- Engine/registry are the only detection files touched besides the 3 new ones + values.py + policy.
- `from __future__ import annotations`; type hints; docstrings. detection imports only domain+policy.

## Done criteria
- `python -m pytest -q` green; demo finding_count unchanged (still 9 as of 2026-06-21).
- Report files created/modified + results.
