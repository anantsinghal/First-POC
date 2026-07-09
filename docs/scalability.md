# Scalability & reusability - honest assessment + the path

Target reality: any org - hundreds of objects, deep metadata, **millions+ of records**. Two
properties must hold: **reusable across all orgs** and **scales horizontally while staying truthful**.

## 1. Reusability - DONE (and it already "scales" to any number of objects)
Detection is 100% **metadata-driven**: no object/field/value/stage is hardcoded in code. Org-specific
knowledge lives entirely in **data** - the metadata catalog (schema from `describe`) and the rule
catalog. Supporting a new org, a new custom object, or a new relationship = **ingest more metadata,
write no code**. This is the load-bearing property for "works across all orgs" and it is real today
(proven on standard + custom objects, and on a never-before-seen `Project__c`).

## 2. Execution scale - HONEST current state (updated 2026-07-09 after the measured 1M proof)
The streaming path now examines a million records in one pass with bounded memory — MEASURED, see
"Measured proof" below. Remaining limits (named, not hidden):

| Area | Today | Remaining limit |
|---|---|---|
| Extraction | Bulk API 2.0 streaming (mock-tested) + streamed SQLite/CSV | live Bulk tuning pending a real big org |
| Detection | tier-0 per batch + streamed DuckDB stats + sampled ML, checkpointed | pattern mining + some graph checks still deferred at scale |
| Persistence | set-based bulk upserts, per-batch checkpoints | SQLite is single-writer |
| Orchestration | `OrgScanOrchestrator` discovers/sizes/routes objects **sequentially**, budget-aware | parallel scans need PostgreSQL (SQLite is single-writer); deferred until a real org needs it |
| Orphan resolution | parent-Id existence probe capped (`parent_id_probe_limit`) | may miss orphans beyond the cap |

**Most important rule, enforced now:** a capped/sampled scan must never *imply* completeness. As of
this phase, a scan that hits the record cap is reported as **PARTIAL / "sampled"** with an explicit
reason ("only the first N records examined; not org-complete"). The system can be incomplete, but it
will not lie about it. (Matches blueprint doc 15 gate: prohibit "complete" labels unless configured
scope succeeds.)

## 3. The scalable architecture (target; aligns with blueprint docs 06 & 15)
1. **Extraction:** Bulk API 2.0 query jobs, chunked; stream results to a **columnar staging store
   (Parquet)** partitioned `tenant/org/object/extract_date`. Never hold a whole object in memory.
2. **Two-pass / online detection:** pass 1 computes aggregates over the columnar store via SQL
   (DuckDB/warehouse) and streaming/approximate stats (counts, quantiles, frequent-itemset supports,
   per-cohort moments); pass 2 flags records in chunks. ML trains on a per-cohort **sample**, then
   scores in batches.
3. **Set-based persistence:** bulk upsert findings (`executemany` / staging table), not per-record.
4. **Scan planner & tiering:** before scanning, estimate object size, selected width, relationship
   fan-out, API cost. Process in tiers - (a) metadata/schema checks (cheap, every object), (b) aggregate
   profiles, (c) candidate generation, (d) expensive relationship/ML only on eligible cohorts/records.
   Enforce per-tenant API + compute budgets.
5. **Org-wide orchestration:** discover all objects, prioritize by importance/size, schedule, run
   **incrementally** (overlapping `SystemModstamp` windows + a record-state ledger), reconcile with
   periodic full scans. Idempotent, resumable, manifest-backed.
6. **Truthful coverage everywhere:** every scan reports included vs skipped/inaccessible/capped scope.
7. **Storage tiers (inside the customer boundary):** PostgreSQL (catalog, scans, findings, feedback,
   metrics) + Parquet/object storage (ephemeral raw/feature staging, purged) + DuckDB/warehouse
   (analytics).

## 4. Sequenced roadmap (no overengineering ahead of need)
1. OK Honest sampled/capped coverage (this phase).
2. Set-based finding persistence (`executemany`).
3. Scan planner + tiering (cheap checks first; ML/graph gated) + per-tenant budgets.
4. [x] Bulk API 2.0 extraction ✅ (mock-tested; live tuning pending real org).
5. [x] Online/2-pass detectors (streaming quantiles, sampled ML) for bounded memory. Statistical detectors now run as a streamed Parquet second pass and ML scores against a deterministic reservoir sample; pattern mining at scale remains deferred.
6. [x] Incremental sync + record-state ledger + reconciliation ✅ (REST + Bulk sources, mock-tested) +
   org-wide scheduler ✅ (`OrgScanOrchestrator`: discover -> size -> route memory/bulk-stream ->
   budget-aware; sequential v1 - SQLite is single-writer, so parallel per-object scans are deferred
   until a real org's object count demands it and PostgreSQL replaces SQLite as the catalog store).

Integration path note: the scan service PROFILES large objects out-of-core - the planner
(`use_columnar_profile`, default >= 100k records) selects staging records to an ephemeral Parquet ->
`ColumnarProfiler` (DuckDB) -> purge, with identical Profile output (parity-verified). A STREAMING
source (`CsvSource.iter_batches`) + a bounded-memory PROFILE-ONLY scan (`ScanService.profile_source`,
endpoint `POST /api/scans/profile`) now stream records straight into Parquet (never fully materialised)
and profile out-of-core, no findings. Tier-0 streaming/columnar DETECTION passes (per-batch schema/rule checks via /api/scans/database/stream) now extend into a second streamed Parquet pass for exact DuckDB-backed stats plus sampled Isolation Forest scoring, with bounded memory and per-batch finding checkpoints. Pattern mining and some graph checks remain deferred by design.

The invariant to protect through all of this: **detection logic stays generic and metadata-driven; only the data plane (extraction/storage/orchestration) grows.** That keeps the product reusable across every org while it scales.

## Measured proof

Executable scale proof (black-box stream scan over a seeded SQLite deals table with 9 planted
anomalies at head/middle/tail). Reproduce with:

```powershell
python -m tools.scale_proof --rows 1000000 --seed 42 --out scale-proof.json
```

Measured on 2026-07-09 (Windows laptop, seed 42, machine otherwise idle):

| metric | value |
|---|---|
| rows | 1,000,000 |
| row_count | 1,000,000 (complete pass, not sampled) |
| duration_s | **126.7** (~7,900 records/s end-to-end incl. generation-free scan, stats + sampled-ML passes) |
| peak_rss_mb | **338.6** (bounded — O(batch), not O(N); materialising 1M records would need GBs) |
| planted / matched | **9 / 9** (picklist, orphan-FK, extreme amounts at head / middle / tail) — missing = 0 |
| finding_count | 29,567 (9 planted contract/stat hits + statistical/ML candidates: ~2% IF contamination by design) |
| batches | 200 (5,000-record batches, per-batch finding checkpoints) |

CI always exercises a 20k-row smoke (`tests/test_scale_proof.py`). The 1M proof is opt-in via
`RUN_SCALE_PROOF=1`. Scheduling a recurring scan is documented in
[`docs/scheduling.md`](scheduling.md) (`python -m tools.scheduled_scan …`).
