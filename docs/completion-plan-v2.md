# Completion Plan v2 — verified audit + executable phases to 100%

**Audited 2026-07-10 by Claude (architect), every finding verified against code — file:line evidence
included.** This supersedes the gap list in `completion-plan.md` (whose phases 58–61 are done). It is
written so ANY implementing agent (Sonnet 5, Opus 4.8, Grok, Codex) can execute each phase from its
PROMPT block alone.

---

## Part 0 — Agent contract (read first, applies to EVERY phase)

You are the IMPLEMENTER on a FastAPI "universal data-anomaly detector" (layered SOLID; conventions in
`CLAUDE.md`; reusable test helpers in `tests/harness.py`). The architect reviews your work.

RULES
1. Implement the phase EXACTLY as specified. No redesigns, no scope additions, no drive-by refactors.
2. HARD boundary unless the phase says otherwise: NO changes under `app/detection/`,
   `app/profiling/`, `app/coverage/`, `app/planning/`, `app/policy/`. No new dependencies.
3. All existing tests pass UNMODIFIED unless the phase explicitly lists a test to update (with reason).
   `tests/test_scanner.py` is the API-compatibility contract — response SHAPES must stay
   backward-compatible (additive keys/headers only).
4. Run: `python -m pytest -q` (full) and the phase's targeted tests. The one-command health gate is
   `python -m pytest tests/test_healthcheck.py -q`.
5. Preserve UTF-8 exactly in `app/static/*` (no re-encoding of existing `·`/`▲`/`×` characters).
6. End your reply with ONLY:
   `===REPORT===` / `STATUS: DONE|BLOCKED` / `FILES: <changed>` / `TESTS: <pytest tails>` /
   `CONFIRM: <each item the phase asks>` / `NOTES: <1 line max>` / `===END===`
7. If a spec conflicts with reality (missing symbol, failing assumption), STOP → BLOCKED with the
   exact mismatch. Never loosen a test to get green.

---

## Part A — Verified findings (the problems)

| # | Sev | Finding | Evidence | Impact |
|---|-----|---------|----------|--------|
| F1 | **CRIT** | Finding identity has NO source dimension: `fingerprint = hash(object, record, rule, field)` | `app/domain/models.py:46-48` | Two different databases both containing table `orders` with `id=1` (or two Salesforce orgs with colliding record ids) produce THE SAME fingerprint → findings from different sources silently overwrite each other on upsert; feedback/suppression bleed across sources. Confirmed real risk: this deployment already scanned 5 orgs + multiple SQLite files. |
| F2 | **CRIT** | Same scoping hole everywhere history is read by `object_name` only: previous-scan lookup (drift + quality trend), `resolve_for_records`, `sync_state`, `record_ledger` | `app/persistence/repositories.py:80, 350, 481, 519, 537` | Drift compares `orders` from db A against `orders` from db B; reconcile can resolve findings of a DIFFERENT source's records; watermarks collide across orgs. |
| F3 | **CRIT** | Scans run synchronously ON the event loop — no `to_thread`/executor anywhere | grep `to_thread|run_in_executor` over `app/` = empty; `run_scan`/`stream_scan` bodies are CPU/SQLite-bound | During the measured 1M scan (127s) the server cannot answer ANYTHING — dashboard freezes, and `/api/health` times out → the Docker HEALTHCHECK (5s timeout, 3 retries) marks the container unhealthy mid-scan. A monitoring product that dies while monitoring. |
| F4 | **HIGH** | SQLite opened with defaults: no WAL, no busy_timeout | `app/persistence/database.py` `connect()` — grep `WAL|busy_timeout` = empty | Any concurrent write (scan checkpoint) + read (dashboard poll) can raise `sqlite3.OperationalError: database is locked`. Made worse once F3 is fixed (real concurrency begins). |
| F5 | **HIGH** | Findings API has NO pagination; dashboard renders everything | `app/api/routes_findings.py:16`, `app/services/finding_service.py:29` (no limit/offset) | The measured 1M scan produced **29,567 findings** → one multi-MB JSON response and ~30k DOM cards. Browser dies; API payload unbounded. |
| F6 | **HIGH** | Streamed ML + streamed stats have NO volume control: Isolation Forest flags ~2% of ALL rows; streamed IQR fences at 1.5×IQR flag ~0.7% of gaussian data | `app/services/streaming_stats.py` (no cap; grep cap/top = empty); measured: 29,567 findings on clean-ish 1M rows | Finding flood at scale — thousands of low-value candidates drown the review queue and the ranking model's future training data. |
| F7 | **HIGH** | Incremental delta scans run the FULL detector registry on the DELTA-ONLY population | `app/services/sync_service.py:75` (`run_scan(_Preloaded(dataset))` where dataset = changed records only) | IQR/MAD/rare-combination/pattern/ML baselines computed from e.g. 300 changed records, not the object's true population → statistically INVALID findings with confident-sounding evidence. Correctness bug in the trust-critical path. |
| F8 | **MED** | Bulk path never resolves `related_ids` (`salesforce_bulk_source.py:51,160` hard-None) AND stream_scan's audit `deferred` list does NOT mention `orphan_reference` when it silently can't run | `app/ingestion/salesforce_bulk_source.py:51`; deferred list in `app/services/scan_service.py` (orphan never listed) | Orphan detection is silently absent on exactly the biggest objects — and unreported, which violates the coverage-honesty principle. |
| F9 | **MED** | Reviewer identity is a passthrough that the UI never sets → all labels effectively anonymous | `app/services/finding_service.py:39`; dashboard feedback POST sends no reviewer | Learning-model training data has no provenance; enterprise review requires knowing WHO labeled. |
| F10 | **LOW** | Sampled-coverage off-by-one: `truncated = len(cleaned) >= max_records` — an object with EXACTLY max_records rows is falsely downgraded to "sampled/partial" | `app/ingestion/salesforce_source.py:144` | Cosmetic honesty error (false modesty, not false confidence). |
| F11 | **LOW** | Retention purge never touches `record_ledger`/`sync_state`; no CORS knob; 401s not audited | `repositories.py purge_scan`; `main.py` | Governance/ops polish. |
| F12 | **HUMAN** | The learning model has ZERO real labels; Docker artifacts unverified (no Docker on this laptop); no live big-org run yet | `docs/completion-plan.md` | The goal-word "trained" is still unproven; closure needs a human session (Phase 69 runbook). |

---

## Part B — The phases (execute in order; each PROMPT is self-contained)

### Phase 62 — Concurrency & DB robustness (fixes F3, F4)

**Design.** (a) SQLite: enable WAL + busy_timeout on every connection. (b) Scans must not block the
event loop: for sources whose work is fully synchronous (CSV, multi-CSV, SQLite, demo, REST
Salesforce — everything except Bulk's async HTTP), run the whole scan on a worker thread with its own
event loop via `asyncio.to_thread(asyncio.run, ...)`. For the Bulk/org paths (real awaits inside),
offload the CPU-heavy per-batch work instead.

**PROMPT (copy-paste to the agent, after Part 0):**
```
TASK Phase 62 (concurrency + DB robustness). Two defects: sqlite has no WAL/busy_timeout, and scans
run on the event loop so /api/health stalls during a long scan (Docker healthcheck would kill the
container). Implement EXACTLY:
1) app/persistence/database.py connect(): after sqlite3.connect(...), execute
   'PRAGMA journal_mode=WAL' and 'PRAGMA busy_timeout=5000' (and 'PRAGMA synchronous=NORMAL').
   Docstring: WAL enables concurrent readers during writer checkpoints; busy_timeout replaces
   instant 'database is locked' errors with a 5s wait.
2) NEW app/api/offload.py: `async def run_offloaded(coro_factory)` -> runs
   `asyncio.to_thread(lambda: asyncio.run(coro_factory()))` and returns its result. Docstring: gives
   sync-work-heavy scan coroutines their own loop on a worker thread so the server loop stays live.
3) In app/api/routes_scans.py ONLY these endpoints wrap their scan call with run_offloaded, passing a
   lambda that constructs the coroutine: /api/scans/demo, /api/scans/upload, /api/scans/multi,
   /api/scans/profile, /api/scans/database, /api/scans/database/stream, /api/scans/salesforce,
   /api/scans/salesforce/incremental, /api/scans/salesforce/reconcile. Example:
   `return await run_offloaded(lambda: container.scan_service.run_scan(source))`.
   DO NOT wrap the bulk or org endpoints (they hold async HTTP clients bound to the outer loop).
4) For bulk/org loop relief: in app/services/scan_service.py stream_scan, wrap ONLY the per-batch
   CPU body call `_process_batch(b)` for the ASYNC branch as
   `await asyncio.to_thread(_process_batch, b)` (the sync branch runs inside run_offloaded already);
   same for the pass-2 loops (flag_batches/score_ml_batches iteration bodies) in the async-branch
   context — if restructuring is risky, wrapping each `upsert_many+commit` iteration in to_thread
   with the batch is acceptable; state your choice in CONFIRM.
5) tests/test_concurrency.py (NEW):
   a. WAL: open a Database in tmp_path, connect, assert `PRAGMA journal_mode` returns 'wal'.
   b. Liveness: build the app via tests.harness.api_client. Using anyio/asyncio + httpx.AsyncClient
      (ASGITransport against app.main.app), start a database/stream scan of a generated 60k-row
      SQLite table (reuse the generator pattern from tests/test_stream_detection.py) as a background
      asyncio task, then WHILE it runs issue GET /api/health with a 3-second timeout and assert 200
      arrives before the scan task completes. Assert the scan then finishes 'completed'.
   c. Concurrent read during write: start the same streaming scan, and during it call
      GET /api/scans (dashboard read) — assert 200, no 'database is locked' error.
6) Run python -m pytest tests/test_concurrency.py -q and python -m pytest -q.
CONFIRM: (a) journal_mode=wal proof; (b) health responded mid-scan (timing numbers); (c) which
offload option you chose for the async-branch batches; (d) full-suite count.
```

### Phase 63 — Source identity: fingerprint v2 + scoped history (fixes F1, F2)

**Design.** Introduce `source_key` — a stable, normalized identity of WHERE data came from — derived
from the existing `source_name` (`salesforce:host/Object` → `salesforce:host`;
`sqlite:file.db/table` → `sqlite:file.db`; csv uploads → `csv:<object_name>`; else the source_name
verbatim). Stored fingerprints become source-scoped; all history lookups become
`(source_key, object_name)`. Existing rows are migrated in place (all recompute inputs are already
stored columns). Domain `Candidate.fingerprint` is UNCHANGED (engine stays source-blind); the
persistence layer composes the stored value.

**PROMPT:**
```
TASK Phase 63 (source-scoped identity). Today hash(object,record,rule,field) collides across sources
(two dbs both with table 'orders' id=1) and ALL history lookups scope by object_name only —
cross-source contamination of findings/feedback/drift/watermarks. Implement EXACTLY:
1) NEW app/persistence/source_key.py: `def source_key_from_name(source_name: str) -> str` —
   normalize: take the part before the LAST '/' when the name matches '<scheme>:<rest>/<entity>'
   (salesforce:host/Obj -> salesforce:host ; salesforce-bulk:host/Obj -> salesforce-bulk:host ;
   sqlite:file.db/table -> sqlite:file.db); names without that shape (demo csv 'demo_...csv',
   'multi:...', 'stream:...') -> return the whole name prefixed 'src:'. Pure function + docstring.
2) Schema (database.py setup()): ALTER-safe migration — findings gains column source_key TEXT NOT
   NULL DEFAULT ''; scan_manifests gains source_key TEXT NOT NULL DEFAULT ''; sync_state and
   record_ledger get NEW composite tables? NO — simplest correct: add source_key column to sync_state
   and record_ledger and rebuild their PRIMARY KEYs via table-rebuild migration (CREATE new, INSERT
   SELECT with source_key='', DROP old, RENAME). Use PRAGMA user_version: if user_version < 2 run the
   migration function then set user_version=2. Migration for findings/scan_manifests: fill
   scan_manifests.source_key = source_key_from_name(source_name); findings.source_key from its
   scan's manifest (JOIN on scan_id; orphaned findings -> 'src:legacy'); THEN recompute every
   finding's fingerprint as stable_hash({'source': source_key, 'object': object_name,
   'record': record_id, 'rule': finding_type, 'field': field_name}) — all columns exist. On UNIQUE
   collision during recompute keep the row with the latest last_seen_at, delete the other, and count
   deletions (return them from the migration; log into audit as event 'identity_migration').
3) repositories.py:
   - upsert/upsert_many: new REQUIRED kwarg source_key; stored fingerprint computed as in (2) from
     candidate fields + source_key (Candidate.fingerprint property itself stays untouched — engine
     source-blind). insert_running gains source_key kwarg, stored on the manifest.
   - previous_scan_id, sync get/set watermark, ledger_upsert_many/active_ids/mark_deleted,
     resolve_for_records: all gain source_key and add 'AND source_key=?' / composite scoping.
4) scan_service (run_scan/stream_scan/profile_source): compute src_key =
   source_key_from_name(dataset.source_name or derived stream name) once; pass through to
   insert_running + upsert_many + save_quality is per-scan (no change) ; drift/quality services:
   previous_scan_id callers pass the manifest's source_key (add the column to get_scan_detail
   output — additive key). sync_service passes source.— derive from source_name the same way.
5) Tests tests/test_source_identity.py (NEW):
   a. Unit: source_key_from_name for the 5 documented shapes.
   b. THE collision test: two different tmp SQLite dbs each with table 'orders' one bad picklist row
      id=1; scan BOTH via harness.run_scan-style containers (same data dir!); assert TWO distinct
      findings exist (different fingerprints), not one overwritten row.
   c. Drift isolation: scan db-A orders twice (drift baseline exists), scan db-B orders once; assert
      db-B's scan drift baseline_scan_id is None or a db-B scan — never db-A's (call drift service).
   d. Migration: build a v1-shaped db by inserting a finding row with source_key='' and old-format
      fingerprint directly via SQL, set user_version=1, run setup(); assert source_key filled,
      fingerprint recomputed, user_version=2.
   e. Feedback survival: label a finding, re-run the same source scan, assert suppression still
      applies (fingerprint stability within a source).
6) Existing tests: repository-level direct callers of upsert_many/insert_running in tests will need
   the new kwarg — you MAY update ONLY those call sites mechanically (list every touched test file in
   CONFIRM); assertions themselves must not weaken.
7) python -m pytest tests/test_source_identity.py -q then python -m pytest -q.
CONFIRM: (a) collision test proves two sources coexist; (b) migration test passes with recomputed
fingerprints; (c) list of test files whose CALL SITES you updated; (d) full-suite count.
```

### Phase 64 — Pagination + finding-flood control (fixes F5, F6)

**PROMPT:**
```
TASK Phase 64 (pagination + volume control). The measured 1M scan produced 29,567 findings: the API
returns them in ONE response and the dashboard renders ~30k DOM nodes; streamed ML flags ~2% of all
rows and streamed IQR ~0.7% by construction. Implement EXACTLY:
1) FindingRepository.list gains limit:int|None=None, offset:int=0 (SQL LIMIT/OFFSET after existing
   ORDER BY) and a new count(conn, scan_id=None, status=None) -> int with the same WHERE logic.
2) FindingService.list_findings passes them through; NEW count_findings(...).
3) routes_findings GET /api/findings: query params limit (default 200, ge=1, le=1000) and offset
   (default 0, ge=0). RESPONSE STAYS A BARE LIST (tests/test_scanner.py contract) — expose the total
   via response header 'X-Total-Count' (use fastapi Response param). Ranking still applies to the
   returned page.
4) Dashboard app/static/app.js: refresh() fetches with limit=200; if X-Total-Count > shown, render a
   '.load-more' button under #findings appending the next page (offset += 200), and show
   'showing N of TOTAL' in #findingCount. Keep all existing card logic; UTF-8 untouched.
5) Streamed volume control in app/services/streaming_stats.py + scan_service:
   a. StreamingStatsPass.flag_batches gains iqr_multiplier param; scan_service passes
      STREAMED_IQR_MULTIPLIER = 3.0 (module constant, docstring: far-outlier fences at scale; the
      in-memory detector keeps 1.5 — document the intentional difference in evidence 'why').
   b. ML cap: scan_service collects streamed ml candidates into a bounded top-K selection by
      |anomaly_score| (K = MAX_STREAMED_ML_FINDINGS = 500 module constant) BEFORE upserting —
      simplest correct: accumulate (score, candidate) into a heap during score_ml_batches iteration,
      upsert only the final top-K after the loop (they are small objects; 500 cap bounds memory).
      Scan notes must append: 'ml candidates capped at K of N flagged' when capping occurred, and
      the audit detail gains ml_flagged_total + ml_kept.
6) tests/test_pagination_flood.py (NEW): (a) seed a scan with 350 synthetic findings via
   upsert_many; GET /api/findings?scan_id=..&limit=100&offset=300 -> 50 items, X-Total-Count=350;
   default limit returns 200. (b) streamed scan over a 20k-row table seeded so ML would flag >600
   rows (e.g. inject 700 mild multivariate oddballs): assert ml_anomaly persisted count <= 500, notes
   mention the cap, audit has ml_flagged_total >= persisted. (c) far-fence: value at 2x IQR beyond Q3
   is NOT flagged by the streamed pass but 9e9 still is.
7) Existing healthcheck asserts findings non-empty — unaffected. python -m pytest
   tests/test_pagination_flood.py -q then full suite.
CONFIRM: (a) header-based total + bare-list contract intact (test_scanner passed unmodified);
(b) cap enforced numbers from test; (c) far-fence behavior; (d) full-suite count.
```

### Phase 65 — Incremental delta correctness (fixes F7)

**PROMPT:**
```
TASK Phase 65 (incremental statistical validity). sync_service.incremental_scan feeds ONLY changed
records into run_scan, so IQR/MAD/rare-combination/pattern/ML compute baselines from the delta —
statistically invalid findings. Fix EXACTLY:
1) ScanService.run_scan gains keyword-only param include_override: frozenset[str]|None = None; when
   not None, call self.registry.detect(ctx, include=include_override) INSTEAD of the planner's
   included_ids, and complete() notes append ' (delta scan: contract/rule checks only; statistical
   and ML detectors skipped — population is changes-only)'; audit detail gains 'delta_scan': True
   and deferred_detectors = sorted(all DETECTOR_TIERS ids minus the override set).
2) sync_service.incremental_scan (BOTH the load()+run_scan path and the streaming path): build
   DELTA_INCLUDE = frozenset(id for id,t in DETECTOR_TIERS.items() if t==0) | {'orphan_reference'}
   and pass include_override=DELTA_INCLUDE (streaming path: stream_scan already runs tier-0-only in
   pass 1 — but its pass-2 stats/ML MUST BE SKIPPED for incremental: add stream_scan keyword
   skip_statistical_pass: bool = False, set True from the incremental wrapper; when True do not run
   flag_batches/score_ml_batches and add the same delta note). reconcile() (full pull) stays FULL
   registry — unchanged.
3) tests/test_incremental_delta.py (NEW): mock-transport incremental fixture (reuse
   tests/test_incremental_sync.py patterns) where the delta returns 60 records containing one
   9,000,000 Amount among ~100k-mean values: assert the incremental scan yields NO numeric_outlier /
   robust_outlier / ml_anomaly / rare_combination findings, notes contain 'delta scan', audit
   delta_scan True + those ids in deferred_detectors; a bad picklist in the same delta IS flagged
   (tier-0 still works). Then reconcile() full pull with the same extreme value DOES yield
   numeric_outlier.
4) Full suite must stay green (existing incremental tests assert only tier-0-type findings and
   watermark mechanics — verify, and if one asserts a statistical finding from a delta, that test is
   asserting the BUG: update it and justify in CONFIRM).
CONFIRM: (a) delta produced zero statistical findings + honest note/audit; (b) reconcile still
produces them; (c) any updated existing test + why; (d) full-suite count.
```

### Phase 66 — Bulk orphan honesty + optional resolution (fixes F8)

**PROMPT:**
```
TASK Phase 66 (bulk orphan gap). SalesforceBulkSource never resolves related_ids (hard None) so
orphan_reference silently cannot run on the biggest objects, AND stream_scan's deferred list never
mentions it — an honesty violation. Implement EXACTLY:
1) HONESTY (required): in scan_service.stream_scan, when related_ids is empty/None AND the schema
   has at least one REFERENCE field, append 'orphan_reference' to the audit deferred list and add
   '; orphan checks deferred (no reference resolution on this path)' to notes.
2) RESOLUTION (required, bounded): after the Parquet writer closes (columnar branch only) and BEFORE
   stats pass, if the source is missing related_ids but schema.reference_fields() is non-empty and
   the source exposes a salesforce client (hasattr(source,'client') and it has query_records):
   for each single-target reference field present in cols, DuckDB 'SELECT DISTINCT "<f>" FROM
   read_parquet(...) WHERE trim("<f>")<>'' LIMIT 2001' — if distinct count > 2000 skip that field
   (audit note 'orphan probe skipped <field>: too many distinct parents'); else probe existence via
   the EXISTING pattern (SELECT Id FROM <target> WHERE Id IN (...) in 200-id batches, try/except
   SalesforceError -> skip field). Build related_ids, then run ONE extra flagging pass: iterate the
   parquet in batches (reuse pyarrow iter pattern) building a DetectionContext with
   include={'orphan_reference'} via self.registry.detect, upsert+commit per batch (checkpointed).
   Update deferred/notes accordingly (orphan ran). Everything guarded so SQLite/CSV streaming paths
   are untouched (they already pass related_ids).
3) tests/test_bulk_orphan.py (NEW): extend the bulk mock fixture (tests/test_bulk_api.py patterns) —
   describe includes AccountId reference->Account; CSV pages contain 3 distinct AccountIds one of
   which the existence probe (mock handler) omits; stream_scan the bulk source: assert an
   orphan_reference finding for the dangling id, and audit deferred does NOT list orphan_reference.
   Second test: handler where the existence probe raises 400 -> no orphan findings, deferred DOES
   list orphan_reference (honesty path).
4) python -m pytest tests/test_bulk_orphan.py tests/test_bulk_api.py -q then full suite.
CONFIRM: (a) dangling id caught on the bulk path; (b) failure path honestly deferred; (c) distinct
cap respected (state the guard); (d) full-suite count.
```

### Phase 67 — Reviewer identity + auth-failure audit (fixes F9, part of F11)

**PROMPT:**
```
TASK Phase 67 (review provenance + auth audit). Labels are anonymous (UI never sends reviewer) and
failed auth attempts leave no trace. Implement EXACTLY:
1) Dashboard: on first feedback click, if localStorage 'scanner_reviewer' is empty, prompt() once for
   a display name (fallback 'anonymous'); store it; include reviewer in the POST body of
   /api/findings/{id}/feedback (the field already exists end-to-end — verify the pydantic input model
   default and keep backward compat). Show the active reviewer name subtly in the header subtitle
   ('reviewing as X' span, id #reviewerTag).
2) MetricsService.overview(): additive key 'labels_by_reviewer' = {reviewer: count} via a new
   FeedbackRepository.reviewer_counts(conn).
3) Auth audit: in app/api/security.py, on mismatch (only when a key IS configured), append an audit
   row event='auth_failed', actor='unknown', detail={'path': request.url.path} — NEVER any key
   material — using a module-level Database/AuditRepository is NOT available there; instead raise as
   today but attach request.state.auth_failed=True and add a tiny middleware in main.py that, when
   response.status_code==401 and getattr(request.state,'auth_failed',False), appends the audit row
   via app.state.container.audit_service (wrap in try/except: auditing must never break the
   response). Cap noise: only audit at most once per 10 seconds per path (module-level dict
   last_logged[path]).
4) tests/test_reviewer_audit.py (NEW): (a) feedback POST with reviewer 'alice' -> feedback row +
   metrics labels_by_reviewer alice=1; (b) with SCANNER_API_KEY set, one bad-key request ->
   audit contains auth_failed with the path and NO key text anywhere in detail; burst of 5 bad
   requests within the window -> at most 2 audit rows (throttle works).
5) Full suite green; UTF-8 untouched.
CONFIRM: (a) reviewer flows end-to-end into metrics; (b) auth_failed audited without secrets +
throttled counts; (c) full-suite count.
```

### Phase 68 — Housekeeping basket (fixes F10, rest of F11)

**PROMPT:**
```
TASK Phase 68 (housekeeping). Three small verified issues. Implement EXACTLY:
1) Sampled off-by-one: app/ingestion/salesforce_source.py queries max_records via
   client.query_records(soql, max_records=self.max_records) then sets truncated=len>=max. Change to
   request max_records+1 records, set truncated = len(records) > self.max_records, and slice
   records = records[:self.max_records] BEFORE cleaning. Mirror the same +1/slice pattern in
   SqliteTableSource.load/iter_batches ONLY IF it does not already fetch limit+1 (READ it first —
   it does; confirm and leave it). Add test: mock returning exactly max_records rows -> truncated
   False, coverage 'good'; max+1 -> truncated True 'partial'.
2) Retention: POST /api/admin/purge response gains 'ledger_pruned': after purging scans, delete
   record_ledger and sync_state rows whose (source_key, object_name) no longer has ANY scan in
   scan_manifests (single SQL each with NOT EXISTS). Test: purge everything for an object -> its
   ledger rows gone; an object with remaining scans keeps them.
3) CORS knob: in main.py, if env SCANNER_CORS_ORIGINS is set (comma-separated), add FastAPI
   CORSMiddleware with those origins, allow_headers=['X-API-Key','Content-Type'], methods GET/POST.
   Unset = middleware not added (today's behavior). Test: with env set via monkeypatch+reload,
   OPTIONS preflight to /api/findings returns the origin header; without env, no CORS headers.
4) Full suite green.
CONFIRM: (a) exact-boundary no longer 'sampled' (test result); (b) ledger pruning counts; (c) CORS
on/off proof; (d) full-suite count.
```

### Phase 69 — Goal-closure runbook (addresses F12 — the human mile)

**PROMPT:**
```
TASK Phase 69 (goal closure documentation — docs only, no app/ or tests/ changes). Create
docs/goal-closure-runbook.md with EXACT operator procedures (concrete commands, expected outputs):
1) LABELING SESSION (the step that activates the learning model): start server; for each connected
   org alias run the token flow (sf org display --json -> SF_INSTANCE_URL/SF_ACCESS_TOKEN env ->
   restart server) and POST /api/scans/salesforce for Account/Contact/Opportunity/Case + custom
   objects; then in the dashboard label AT LEAST 40 findings with BOTH classes represented >= 8
   (real_issue / false_positive / valid_exception), with guidance on judging each finding family;
   click 'Train model'; verify the Learned-ranking panel flips to LEARNED MODEL and finding cards
   show 'model: N% real'; re-scan one org and confirm rank_basis=model ordering.
2) LIVE SCALE RUN: choose the biggest available object; POST /api/scans/salesforce/bulk (or org
   orchestrator with stream_threshold) ; what to record into docs/scalability.md (duration, rows,
   findings, deferred list).
3) DOCKER VERIFICATION on a Docker-capable host: the exact 6 steps from
   .claude/plans/phase-61-deployment.md section 5, plus removing the UNVERIFIED banner from
   docs/deployment.md ONLY after they pass.
4) POSTGRES DECISION GATE: when parallel per-object scans are needed, the repository layer swap
   outline (what changes: Database/connect + SQL dialect touchpoints list — enumerate the actual
   sqlite-specific SQL: PRAGMA calls, INSERT OR REPLACE, ON CONFLICT clauses, rowid usages — grep
   and list them with file:line so the future implementer has the exact inventory).
5) Update README Status section: point to this runbook as 'the last mile'; update
   docs/completion-plan.md marking which findings F1-F11 are closed by phases 62-68.
CONFIRM: (a) runbook sections complete with copy-paste commands; (b) sqlite-specific SQL inventory
count; (c) README/completion-plan updated.
```

---

## Part C — Closure matrix (how we know we're done)

| Finding | Closed by | Proof artifact |
|---|---|---|
| F1, F2 | Phase 63 | collision + drift-isolation + migration tests green |
| F3, F4 | Phase 62 | health-responds-mid-scan test; WAL pragma test |
| F5, F6 | Phase 64 | pagination header test; ML cap ≤500 test; far-fence test |
| F7 | Phase 65 | delta-produces-no-stats test; reconcile-still-does test |
| F8 | Phase 66 | bulk dangling-id caught; failure path honestly deferred |
| F9, F11 | Phases 67, 68 | reviewer-in-metrics; auth_failed audited; ledger pruning; CORS |
| F10 | Phase 68 | exact-boundary coverage 'good' |
| F12 | Phase 69 + HUMAN | learned-model active on real labels; scalability.md live numbers; Docker banner removed |

**Definition of 100%:** phases 62–68 merged with all listed tests green and the full suite passing;
Phase 69 runbook executed by a human (labels → model active; live run recorded; Docker verified on a
capable host). At that point every word of the goal — complete data, accurately, patterns + ML
trained on data, enterprise-grade security, any org/database — is either demonstrated by a test or
measured in a checked-in document.
