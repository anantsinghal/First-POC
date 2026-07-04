# Scalability & reusability - honest assessment + the path

Target reality: any org - hundreds of objects, deep metadata, **millions+ of records**. Two
properties must hold: **reusable across all orgs** and **scales horizontally while staying truthful**.

## 1. Reusability - DONE (and it already "scales" to any number of objects)
Detection is 100% **metadata-driven**: no object/field/value/stage is hardcoded in code. Org-specific
knowledge lives entirely in **data** - the metadata catalog (schema from `describe`) and the rule
catalog. Supporting a new org, a new custom object, or a new relationship = **ingest more metadata,
write no code**. This is the load-bearing property for "works across all orgs" and it is real today
(proven on standard + custom objects, and on a never-before-seen `Project__c`).

## 2. Execution scale - HONEST current state
The pipeline today is **single-object-per-scan, in-memory, and record-capped** (`max_records`, API
cap 2000). It is **correct on the records it sees**, but it does NOT yet examine millions of records in
one pass. Specific limits (named, not hidden):

| Area | Today | Limit at millions of records |
|---|---|---|
| Extraction | REST/SOQL pagination into a Python list | loads all rows into memory; no Bulk API/streaming |
| Detection | in-memory passes; IF/pattern/IQR build full matrices | O(N) memory; not chunked/online |
| Persistence | findings upserted one-by-one | many SQL statements per scan |
| Orchestration | one object per scan call | no org-wide planner/prioritization across hundreds of objects |
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
5. Online/2-pass detectors (streaming quantiles, sampled ML) for bounded memory.
6. [x] Incremental sync + record-state ledger + reconciliation ✅ (REST source, mock-tested; org-wide scheduler remains v2-live).

Integration path note: the scan service PROFILES large objects out-of-core - the planner
(`use_columnar_profile`, default >= 100k records) selects staging records to an ephemeral Parquet ->
`ColumnarProfiler` (DuckDB) -> purge, with identical Profile output (parity-verified). A STREAMING
source (`CsvSource.iter_batches`) + a bounded-memory PROFILE-ONLY scan (`ScanService.profile_source`,
endpoint `POST /api/scans/profile`) now stream records straight into Parquet (never fully materialised)
and profile out-of-core, no findings. Tier-0 streaming/columnar DETECTION passes (per-batch schema/rule checks via /api/scans/database/stream) are now implemented with true bounded-memory Parquet teeing and per-batch finding checkpoints; statistical/pattern/ML remain deferred by design.

The invariant to protect through all of this: **detection logic stays generic and metadata-driven; only the data plane (extraction/storage/orchestration) grows.** That keeps the product reusable across every org while it scales.
