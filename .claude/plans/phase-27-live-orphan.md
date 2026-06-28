# Phase 27 — Live cross-object orphan resolution (parent-Id existence checks)

Make `orphan_reference` fire on live Salesforce scans by resolving each reference field's parents.
Approach is PRECISE (no false positives from capping): collect the distinct referenced Ids from the
scanned records, then ask Salesforce which of those Ids actually exist (`SELECT Id FROM <Parent> WHERE
Id IN (...)` in batches). Referenced Ids that don't come back are orphans. Populate `related_ids` with
the existing parent Ids so the Phase-20 detector flags the rest. Mock-tested; CSV path unaffected.

## `app/ingestion/salesforce_source.py`
- `__init__`: add `resolve_references: bool = True`, `parent_id_probe_limit: int = 2000`,
  `id_batch_size: int = 200`.
- In `load()`, AFTER building `cleaned`, `fields`, `schema` (before/around building the dataset):
  - `related_ids: dict[str, frozenset[str]] = {}`.
  - If `self.resolve_references`: for each `fd in schema.reference_fields()` where `fd.name in fields`
    AND `len(fd.reference_to) == 1` AND `fd.reference_to[0] != self.object_name` (self-refs handled by
    the detector via own Ids):
    - `target = fd.reference_to[0]`.
    - `referenced = sorted({clean(r.get(fd.name)) for r in cleaned if clean(r.get(fd.name))})`; if empty
      skip; cap to `self.parent_id_probe_limit`.
    - `existing: set[str] = set()`; for each batch of `self.id_batch_size` ids:
      `inlist = ", ".join("'" + i.replace("'", "") + "'" for i in batch)`;
      `try: rows = await self.client.query_records(f"SELECT Id FROM {target} WHERE Id IN ({inlist})",
      max_records=len(batch))` `except SalesforceError: continue` (skip this target on error — resilient).
      `existing |= {clean(row.get("Id")) for row in rows if clean(row.get("Id"))}`.
    - Merge into a plain dict-of-sets accumulator keyed by `target` (multiple fields may share a target).
  - After the loop, `related_ids = {t: frozenset(ids) for t, ids in accumulator.items()}`.
  - Pass `related_ids=related_ids or None` into the returned `LoadedDataset`.
- Import `SalesforceError` from `..salesforce.client`/`session` as needed. Keep everything else
  (field selection, cleaning, schema, source_hash) unchanged.

## Tests — `tests/test_live_orphan.py` (NEW)
Mock the full path with `httpx.MockTransport`, fake `SalesforceSession`:
- describe "Opportunity": Id(id), Name(string), StageName(picklist active values), AccountId(reference,
  reference_to=["Account"]), Amount(double) — all queryable.
- `/query` (the main SOQL on Opportunity) returns 4 opps: three with valid AccountIds ("001AAA","001BBB",
  "001CCC") and one with "001MISSING".
- The handler must also answer the parent existence query: when the SOQL is `SELECT Id FROM Account
  WHERE Id IN (...)`, return only the records for the valid Ids that appear in the WHERE clause (i.e.
  echo back the ones present EXCEPT "001MISSING"). (Inspect `request.url.params["q"]` to branch.)
- `SalesforceObjectSource(client=SalesforceClient(session, transport=mock), session=session,
  object_name="Opportunity")`, `load()`: assert `dataset.related_ids["Account"]` contains the 3 valid
  Ids and NOT "001MISSING".
- Run `build_default_registry().detect(DetectionContext("Opportunity", ds.records, ds.fields,
  DEFAULT_POLICY, schema=ds.schema, related_ids=ds.related_ids))` and assert exactly one
  `orphan_reference` finding, on the opp whose AccountId == "001MISSING".
- A `resolve_references=False` variant → `dataset.related_ids` is None/empty and no orphan fires.

## Constraints
- `from __future__ import annotations`; type hints; docstrings. No secrets (mock transport only).
- CSV path + all other tests unchanged. Resilient: a failed parent probe skips that target, never fails
  the scan.

## Done criteria
- `python -m pytest -q` green. Report files + results and confirm the mocked live scan flags exactly the
  dangling AccountId as `orphan_reference`.
