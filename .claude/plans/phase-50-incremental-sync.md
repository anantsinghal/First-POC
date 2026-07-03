# Phase 50 — Incremental sync + record-state ledger + reconciliation

## Why
Production monitoring can't re-pull whole objects daily. This adds: a **watermark** per object
(re-scan only records modified since last sync, with a safety overlap), a **record-state ledger**
(what we've seen, when, content hash), and **reconciliation** (a full pull that detects vanished
records and resolves their stale findings). Mock-transport tested; scoped to the REST Salesforce
source for v1 (blueprint doc 15 P0 "scan consistency", minimal honest slice).

HARD CONSTRAINTS: zero changes under `app/detection/`, `app/profiling/`, `app/coverage/`,
`app/planning/`, `app/policy/`. No new deps. Existing tests stay green. Finding fingerprints already
make re-detection idempotent — rely on that.

## 1. Persistence — `app/persistence/database.py` `setup()` add
```
CREATE TABLE IF NOT EXISTS sync_state (
  object_name TEXT PRIMARY KEY, last_watermark TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS record_ledger (
  object_name TEXT NOT NULL, record_id TEXT NOT NULL,
  first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
  content_hash TEXT NOT NULL, is_deleted INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (object_name, record_id)
);
```

## 2. Repository — `app/persistence/repositories.py` — `class SyncRepository`
- `get_watermark(conn, object_name) -> str | None`; `set_watermark(conn, object_name, watermark: str)`
  (INSERT OR REPLACE, updated_at=now()).
- `ledger_upsert_many(conn, *, object_name, entries: list[tuple[str, str]], seen_at: str)` —
  entries are (record_id, content_hash); INSERT new (first_seen=last_seen=seen_at, is_deleted=0) or
  UPDATE last_seen_at/content_hash/is_deleted=0 on conflict (use `INSERT ... ON CONFLICT(object_name,
  record_id) DO UPDATE SET last_seen_at=excluded.last_seen_at, content_hash=excluded.content_hash,
  is_deleted=0` via executemany).
- `ledger_active_ids(conn, object_name) -> set[str]` (is_deleted=0).
- `ledger_mark_deleted(conn, object_name, record_ids: list[str]) -> int` (chunked IN update, return count).
Also add to `FindingRepository`: `resolve_for_records(conn, *, object_name, record_ids: list[str]) ->
int` — chunked UPDATE findings SET status='resolved' WHERE object_name=? AND record_id IN (...) AND
status IN ('open','reopened'); return number updated (use cursor.rowcount accumulation).

## 3. Source support — `app/ingestion/salesforce_source.py`
`SalesforceObjectSource.__init__` gains `modified_since: str | None = None`. In `load()`, when set,
append ` WHERE SystemModstamp >= {modified_since}` to the main SOQL (SystemModstamp is a literal ISO
datetime in SOQL — no quotes). Nothing else changes.

## 4. Service — `app/services/sync_service.py` — `class SyncService`
`__init__(self, *, database, scan_service, sync: SyncRepository, findings: FindingRepository,
audit: AuditRepository)`.
Helper `class _Preloaded:` (module-level, tiny): `def __init__(self, dataset)`; `async def load(self):
return self._dataset` — lets us load once, ledger it, then hand the SAME dataset to
`scan_service.run_scan` without a second network pull.
- `async def incremental_scan(self, source, *, overlap_minutes: int = 5) -> dict`:
  1. `object_name = source.object_name`. Read watermark (own short connect()).
  2. If a watermark exists: compute `since = (parse ISO watermark) - timedelta(minutes=overlap_minutes)`
     and set `source.modified_since = since.strftime('%Y-%m-%dT%H:%M:%SZ')` (UTC). Else full pull.
  3. `dataset = await source.load()`; `scan = await self.scan_service.run_scan(_Preloaded(dataset))`.
  4. In one connect(): `ledger_upsert_many` with entries `[(r["Id"], stable_hash(r)) for r in
     dataset.records if r.get("Id")]`, seen_at=now(); `set_watermark(object_name, now())`; audit event
     `"incremental_scan"` detail `{"object_name":..., "mode": "incremental" if watermark else "initial",
     "changed_records": len(dataset.records), "watermark_overlap_minutes": overlap_minutes}`.
  5. Return the scan detail with two extra keys: `scan["sync_mode"]`, `scan["changed_records"]`.
- `async def reconcile(self, source) -> dict`:
  1. Ensure `source.modified_since = None` (full pull). `dataset = await source.load()`;
     `scan = await self.scan_service.run_scan(_Preloaded(dataset))`.
  2. In one connect(): `current = {r["Id"] for r in dataset.records if r.get("Id")}`;
     `known = ledger_active_ids(...)`; `vanished = sorted(known - current)`;
     `deleted = ledger_mark_deleted(..., vanished)`; `resolved = findings.resolve_for_records(...,
     record_ids=vanished)`; `ledger_upsert_many` for current; `set_watermark(..., now())`; audit
     `"reconciliation"` detail `{"object_name":..., "records": len(current), "vanished": deleted,
     "resolved_findings": resolved}`.
  3. Return scan detail + `{"vanished_records": deleted, "resolved_findings": resolved}` keys.

## 5. Wiring + API
- `app/api/container.py`: build `SyncRepository()`, `sync_service = SyncService(...)`, add
  `sync_service` to Container.
- `app/api/routes_salesforce.py` (or scans — follow layout): `POST /api/scans/salesforce/incremental`
  and `POST /api/scans/salesforce/reconcile`, both taking the existing `SalesforceScanInput`, building
  session/client/`SalesforceObjectSource` from env exactly like `/api/scans/salesforce` (same error
  mapping), calling the respective SyncService method.

## 6. Tests — `tests/test_incremental_sync.py` (NEW)
Fake session + stateful `httpx.MockTransport` (closure counter/mode), describe = Id + Name(required)
+ Status(picklist active A/B):
1. **Initial then incremental**: first main query returns 3 records (Ids 001-003, one with Status="X"
   → picklist_invalid). Run `incremental_scan(source)` (build SyncService from a tmp container's
   parts — container exposes sync_service). Assert: scan completed; ledger has 3 active ids;
   watermark set; audit `incremental_scan` mode "initial". Then create a SECOND source against a
   handler that ASSERTS the SOQL contains "WHERE SystemModstamp >=" and returns 1 changed record
   (Id 002 now Status="B"); run incremental again → assert handler saw the WHERE clause, scan row_count
   1, ledger still 3 active, watermark advanced (>= previous), audit mode "incremental".
2. **Reconcile resolves vanished records' findings**: handler now returns only records 001 and 003
   (002 vanished). Before reconciling assert there exists at least one open finding for record 002?
   (The Status X finding was on e.g. 003; ensure the fixture plants the picklist_invalid on 002 so it
   has an open finding.) Run `reconcile(source)` → assert vanished_records == 1, resolved_findings >= 1,
   ledger shows 002 is_deleted, the 002 finding's status == "resolved", audit `reconciliation` written.
3. **Overlap math**: unit — set watermark to a known ISO; call incremental; assert the SOQL timestamp
   equals watermark minus 5 minutes (parse from the captured query).
4. Repos unit: ledger upsert twice updates last_seen/content_hash not duplicate; mark_deleted count.

## 7. Docs
`docs/scalability.md` roadmap item 6: incremental sync + ledger + reconciliation ✅ (REST source,
mock-tested; org-wide scheduler remains v2-live). README API lines.

## Done criteria
Full suite + healthcheck green; report files changed, test tails, zero engine diffs, and the
reconcile flow (vanished record → ledger deleted → finding resolved) confirmed.
