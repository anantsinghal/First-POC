# Phase 20 — Relationship layer: orphan / dangling-reference detection

Blueprint Graph Analyzer (doc 02 component #9), finding type `relationship_gap` family. First graph
check: a reference field pointing to a parent record that is NOT present in the scanned data (an
orphan / dangling reference). Schema-driven (uses `reference_to` from Phase 17) and ADDITIVE — it
no-ops when it cannot verify (no schema, or the parent object's Ids aren't available), so the demo and
all existing tests are unchanged. Self-references are verifiable from the scan's own Id set; cross-
object references are verifiable when the parent object's Ids are supplied to the scan.

Do NOT modify existing test files except adding the new one; do not change existing detector behavior.

## 1. DetectionContext — `app/detection/base.py`
Add `related_ids: dict[str, frozenset[str]] = field(default_factory=dict)` (map: referenced object API
name → set of that object's record Ids known in this scan). Import `field` from dataclasses. No other
change. (Empty by default → cross-object orphan checks simply skip.)

## 2. Ingestion + service wiring (so the path exists end-to-end)
- `app/ingestion/base.py`: add `related_ids: dict[str, frozenset[str]] | None = None` to `LoadedDataset`
  (optional, last field).
- `app/services/scan_service.py`: when constructing `DetectionContext`, pass
  `related_ids=dataset.related_ids or {}`. (No source populates it yet; populating it from a live org —
  by also querying referenced parents' Ids — is the documented Salesforce-mode follow-up.)
- `csv_source.py` / `salesforce_source.py`: leave `related_ids=None` for now (no behavior change).

## 3. Detector — `app/detection/graph/__init__.py` (empty) and
`app/detection/graph/orphan_reference.py` — `class OrphanReferenceDetector`
- `id: str = "orphan_reference"`; `requirement: ClassVar[CheckRequirement | None] = None`.
- `detect(ctx)`:
  1. If `ctx.schema is None`: return `[]`.
  2. `refs = ctx.schema.reference_fields()`; if none, return `[]`.
  3. `own_ids = frozenset(clean(r.get("Id")) for r in ctx.records if clean(r.get("Id")))`.
  4. For each `fd in refs` where `fd.name in ctx.fields`:
     - Build the set of known parent Ids: for each `t in fd.reference_to`, use `own_ids` if
       `t == ctx.object_name` (self-reference), else `ctx.related_ids[t]` if `t in ctx.related_ids`.
       If NO known set could be assembled (we have no parent data to check against) → SKIP this field
       (coverage: cannot verify, do not guess).
     - `valid = frozenset().union(*known_sets)`.
     - For each record with non-empty Id: `val = clean(record.get(fd.name))`; skip if empty (empty is a
       required/referential concern, not an orphan); if `val not in valid` → emit candidate:
       title `f"Orphan reference in {fd.name}"`, field_name `fd.name`,
       evidence `{"field": fd.name, "reference_to": list(fd.reference_to), "observed_value": val,
       "checked_against": <the target object name(s) used>, "known_parent_count": len(valid),
       "why": f"{fd.name} points to a record ({val}) that is not present in the scanned data — a possible
       orphan / dangling reference."}`.
  - Use `make_candidate(...)`; import `clean` from `app.domain.values`.
  - NOTE caveat in a docstring: assumes consistent 15- vs 18-char Salesforce Ids across the scan (Id
    normalization is a later hardening item per blueprint doc 15).

## 4. Policy — `app/policy/field_policy.py`
Add `"orphan_reference": (Severity.HIGH, 0.90, 0.85)`.

## 5. Registry — `app/detection/registry.py`
APPEND `OrphanReferenceDetector()` (no-ops without schema + parent Ids → demo/existing tests unchanged).

## 6. Tests — `tests/test_graph.py` (NEW)
- Cross-object: build a schema (via `schema_from_dict`) for "Opportunity" with `AccountId` as a
  `reference` field `reference_to=["Account"]`; records with several AccountIds; `related_ids={"Account":
  frozenset({the valid ones})}`; include ONE record whose AccountId is not in the set and ONE with an
  empty AccountId. Assert exactly ONE `orphan_reference` finding (the dangling one; the empty one is NOT
  flagged), with evidence `observed_value` = the bad Id.
- Self-reference: schema for object "Account" with `ParentId` reference_to=["Account"]; records where one
  `ParentId` is not among the records' own Ids → exactly one `orphan_reference` (verified from own Id set,
  no `related_ids` needed).
- No-op cases: `ctx.schema is None` → `[]`; a reference whose target is neither self nor in
  `related_ids` → `[]` (cannot verify).
- Wiring: a fake `DataSource` returning a `LoadedDataset` with schema + records + `related_ids`, run via
  `ScanService.run_scan` (tmp `Database`), assert the resulting findings include `orphan_reference`.
- Demo regression: 11-row demo via `build_default_registry().detect(ctx_without_schema)` still yields the
  same count (orphan_reference no-ops without schema); and the full registry on the demo produces zero
  `orphan_reference`.

## Constraints
- `from __future__ import annotations`; type hints; docstrings. detection/domain stay framework-free.
- Additive; demo (11-row) finding count unchanged (orphan_reference = 0 on the demo).

## Done criteria
- `python -m pytest -q` green; demo finding count unchanged. Report files + results and confirm
  orphan_reference fires on the synthetic dangling reference but no-ops on the demo.
