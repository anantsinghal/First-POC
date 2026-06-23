# Phase 23 — Multi-object relationship plumbing + "missing expected children"

Activate the relationship layer with real related-object data: thread related records through the
context (which also derives `related_ids` so the Phase-20 orphan check lights up cross-object), and add
a `MissingChildrenDetector` that flags parents lacking children when most comparable parents have them.
ADDITIVE: everything no-ops without related data, so the CSV demo and all existing tests are unchanged.

Do NOT change existing detector behavior or existing test files. App-code edits are limited to the
files listed below.

## 1. DetectionContext — `app/detection/base.py`
Add `related_records: dict[str, list[Record]] = field(default_factory=dict)` (map: related object API
name → that object's records). (`field` is already imported for `related_ids`.) No other change.

## 2. Ingestion + service wiring
- `app/ingestion/base.py`: add `related_records: dict[str, list[Record]] | None = None` to
  `LoadedDataset` (optional, last field).
- `app/services/scan_service.py`: when building `DetectionContext`, pass
  `related_records=dataset.related_records or {}` AND derive related_ids if absent:
  `related_ids = dataset.related_ids or {obj: frozenset(clean(r.get("Id")) for r in recs if
  clean(r.get("Id"))) for obj, recs in (dataset.related_records or {}).items()}`. (import `clean` if
  needed.) Pass that `related_ids` too. No other logic change.
- `csv_source.py` / `salesforce_source.py`: leave `related_records=None` (no behavior change).

## 3. Detector — `app/detection/graph/missing_children.py` — `class MissingChildrenDetector`
- `id: str = "missing_children"`; `requirement: ClassVar[CheckRequirement | None] = None`.
- `__init__(self, *, min_records: int = 10, min_coverage: float = 0.8, min_link_overlap: float = 0.5)`.
- `detect(ctx)`:
  1. If not `ctx.related_records`: return `[]`.
  2. `parent_ids = {clean(r.get("Id")) for r in ctx.records if clean(r.get("Id"))}`; if
     `len(parent_ids) < self.min_records`: return `[]`.
  3. For each `(child_object, child_records)` in `ctx.related_records.items()`:
     - **Auto-discover the link field** (the child field pointing to the parent): for each field name in
       the child records (except "Id"), compute the fraction of non-empty values that are in
       `parent_ids`; pick the field with the highest fraction; if that fraction `< self.min_link_overlap`,
       skip this child object (no clear link).
     - Count children per parent: `child_count[parent_id] += 1` for each child record whose
       `clean(record.get(link_field))` is in `parent_ids`.
     - `parents_with_children = number of parent_ids with child_count >= 1`;
       `coverage = parents_with_children / len(parent_ids)`.
     - Only if `coverage >= self.min_coverage` (a strong norm) AND there exist parents with zero
       children: for each parent record (with Id) having zero children, emit a candidate:
       title `f"No related {child_object} records"`, field_name `None`,
       evidence `{"method": "relationship completeness", "child_object": child_object,
       "link_field": link_field, "coverage_rate": round(coverage, 3), "child_count": 0,
       "parent_population": len(parent_ids), "why": f"{round(coverage*100)}% of {ctx.object_name}
       records have at least one related {child_object}; this one has none — possible missing child
       records."}`.
  - Use `make_candidate(...)`; import `clean` from `app.domain.values`.
  - NOTE in docstring: link-field is heuristically auto-discovered by Id overlap (no child schema
    required); assumes related_records contains the full child set for accurate counts.

## 4. Policy — `app/policy/field_policy.py`
Add `"missing_children": (Severity.MEDIUM, 0.80, 0.65)`.

## 5. Registry — `app/detection/registry.py`
APPEND `MissingChildrenDetector()` (no-ops without related_records → demo/existing tests unchanged).

## 6. Tests — `tests/test_missing_children.py` (NEW)
- Strong-norm case: 20 parent "Account" records (Ids acc000..acc019); related "Contact" records linking
  via a field (e.g. `AccountId`) to 17 distinct accounts (so 3 accounts have none); assert
  `MissingChildrenDetector().detect(ctx_with_related_records)` flags exactly those 3 (finding_type
  "missing_children"), evidence has coverage_rate ~0.85 and child_object "Contact".
- Weak-norm: only ~50% of accounts have contacts (coverage < min_coverage) → `[]`.
- No related data → `[]`; fewer than min_records parents → `[]`.
- Wiring: a fake `DataSource` whose `load()` returns a `LoadedDataset` with parent records + a schema +
  `related_records={"Contact":[...]}` (some accounts childless); run via `ScanService.run_scan` (tmp
  `Database`); assert findings include `missing_children`. (This also confirms related_ids derivation.)
- Demo regression: 11-row demo via `build_default_registry().detect(ctx_without_related)` still yields
  the same count; no `missing_children`.

## Constraints
- `from __future__ import annotations`; type hints; docstrings. detection/domain stay framework-free.
- Additive; the 11-row demo finding count stays 10 with zero missing_children.

## Done criteria
- `python -m pytest -q` green; demo finding count unchanged. Report files + results and confirm
  missing_children fires on the synthetic childless parents and no-ops on the demo.
