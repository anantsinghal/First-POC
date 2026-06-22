# Architecture Review And Hardening Plan

## Review Outcome

The core design remains viable. Its most important choices are correct:

- Per-org profiles and models, not one global model
- Batch-first analysis
- Metadata and deterministic checks before ML
- Human review before write-back
- PostgreSQL plus Parquet/DuckDB for the MVP

Before implementation, adopt the guardrails in this document. They prevent the most expensive future failures: unreproducible findings, incomplete scans presented as complete, static analysis presented as runtime truth, and weak tenant isolation.

## Required Design Gates

Do not call the product production-ready until all gates are designed and tested.

1. **Coverage contract**: Every scan reports included, skipped, inaccessible, and unsupported scope with reasons.
2. **Reproducible snapshot**: Every finding identifies immutable metadata, data, feature, rule, policy, and model versions.
3. **Incremental correctness**: Updates, deletes, undeletes, merges, reparenting, and changes during extraction converge correctly.
4. **Runtime boundary**: The product distinguishes static inference from a result proven by supported historical Salesforce semantics.
5. **Tenant isolation**: Isolation is enforced in databases, object storage, queues, logs, caches, backups, and tests.
6. **Golden-org tests**: Versioned Salesforce fixtures cover connector behavior, permissions, formula semantics, idempotency, and known findings.

## Prioritized Gaps

### P0: Scan Consistency And Incremental Change Handling

**Gap**: The design names `SystemModstamp` and `LastModifiedDate` as watermarks but does not define a consistent extraction protocol. A simple watermark query can miss records changed during a job, duplicate a boundary record, leave stale child counts after deletes, or produce findings from a mixed-time snapshot.

**Required architecture**:

- Create an immutable scan manifest before extraction. It must record source org, object/field scope, metadata version, query, requested time range, API version, integration-user identity, and policy version.
- Use a closed scan window plus overlap. Store `window_start`, `window_end`, `overlap_start`, and extraction-run ID. De-duplicate by canonical Salesforce ID and latest source version.
- Maintain a record-state ledger with `first_seen_at`, `last_seen_at`, `last_source_modstamp`, `is_deleted`, source snapshot ID, and content hash.
- Handle soft delete, undelete, merge, lead conversion, reparenting, and cascading relationship changes as explicit events that invalidate dependent profiles and findings.
- Prefer a supported change feed when enabled, but retain periodic reconciliation full scans. A feed and a watermark are not a universal completeness proof.
- Make every extraction and scoring worker idempotent. A retry must never create duplicate profiles, scores, or findings.

**Acceptance test**: Modify, delete, undelete, and reparent records while a full scan runs. The next incremental scan must converge to the same result as a clean full scan.

### P0: Static Metadata Is Not Salesforce Runtime Execution

**Gap**: A validation rule or Flow cannot always be evaluated historically from metadata and a current record. Behavior may depend on prior values, operation type, current user, permissions, custom permissions, time, currency, cross-object formulas, Flow paths, Apex, managed-package code, and external integrations.

**Required architecture**:

- Add `semantic_status` to every metadata-derived rule: `verified_historical`, `partially_supported`, `static_reference_only`, or `unsupported`.
- Build a versioned formula AST/parser and capability matrix. Emit a deterministic violation only when every formula construct and context input is supported.
- Name Flow/Apex outputs `automation_dependency_risk`, never `automation violation`.
- Store rule activation state, source location, source hash, namespace, managed-package flag, and last-seen metadata revision.
- Define a supported-assets matrix for dynamic SOQL, dynamic field access, callouts, packaged code, external objects, reports, integrations, and any other behavior that cannot be faithfully observed.
- Never run customer business logic in the scanner. Optional in-org confirmation rules may come later for high-value cases.

**Acceptance test**: Compare supported formula evaluation against known Salesforce outcomes. Unsupported rules must be labelled as non-deterministic, never silently guessed.

### P0: Coverage, Visibility, And Truthful Product Claims

**Gap**: Field policies and permission errors are mentioned, but there is no coverage contract. A customer could read "org-wide data quality" even when the integration user saw only a subset of records or fields.

**Required architecture**:

- Persist a scan coverage matrix: object, observed records, expected count when available, queried fields, skipped fields, FLS result, queryability, record-type scope, deleted/archive scope, extraction status, and reason.
- Bind each finding to the coverage and field-policy version that produced it.
- Define supported object classes and exclusions, including activities, polymorphic relationships, Person Accounts, knowledge, big objects, external objects, platform events, histories, files, and managed-package objects.
- Display an org-level coverage grade and prohibit "complete" labels unless configured critical scope succeeds.
- Capture the connected-app and integration-user permissions used for each scan. System-user visibility is not business-user visibility.

**Acceptance test**: Removing a field permission must downgrade coverage and suppress dependent checks, rather than produce a misleading missing-value finding.

### P1: Data Plane, Control Plane, And Failure Recovery

**Gap**: The design lists workers and storage but not job ownership, leases, backpressure, retry classes, or recovery semantics.

**Required architecture**:

- Separate the control plane (tenant policy, scan planning, job state, audit) from the data plane (extract, transform, profile, score).
- Model job states: `planned`, `queued`, `running`, `partially_succeeded`, `failed_retryable`, `failed_terminal`, `cancelled`, `completed`, and `superseded`.
- Use per-org concurrency and API-budget limits, durable queues, worker leases/heartbeats, backoff with jitter, dead-letter handling, and resumable chunk checkpoints.
- Store query/request hashes, chunk checksums, source counts, parse errors, and retry history in the scan manifest.
- Classify failures: authentication, permission, quota, schema drift, malformed data, transient network, and unsupported asset.

### P1: Provenance, Versioning, And Finding Lifecycle

**Gap**: `scan_id` alone cannot reproduce a finding or correctly resolve it after the record or metadata changes.

**Required architecture**:

- Version raw metadata, extraction manifests, record snapshots, feature definitions, profiles, rule definitions, parser code, ML models, and score-combination policy.
- Include immutable version IDs in each finding and evidence record.
- Add a stable `finding_fingerprint` for the logical issue, while storing individual scan occurrences separately.
- Support `open`, `acknowledged`, `suppressed_until`, `resolved`, `reopened`, and `obsolete_due_to_metadata_change`.
- Revalidate an open finding against a newer record version before showing it as actionable.
- Separate severity (business impact), confidence (evidence strength/calibration), and priority (severity, confidence, ownership, age, recurrence). One weighted score cannot own all three meanings.

### P1: ML Quality Controls

**Gap**: The ML plan names sensible algorithms but lacks controls against contaminated history, small cohorts, feedback bias, and data leakage.

**Required architecture**:

- Define training-window, cohort-size, history, and new-object policies. Exclude known bad or suppressed records from baselines where appropriate.
- Use temporal holdouts for evaluation. Never let later feedback or later record state leak into an earlier score.
- Do not train unsupervised models for tiny or sparse cohorts. Use rules and robust statistics with a clear UI explanation instead.
- Keep learned patterns as candidates until they meet support, stability-across-windows, and reviewer-acceptance thresholds. A frequent historical defect is still a defect.
- Add data/concept drift monitoring, model rollback, feature-distribution checks, calibrated score thresholds, and retraining approval gates.
- Treat static code-reference evidence as context, not an automatic severity boost. Generic or dead code is common.
- Record review assignment and sampling decisions as well as labels, so feedback bias can be measured.

### P1: Tenant Security Must Reach Every Layer

**Gap**: Tenant IDs and encrypted storage are necessary but are not by themselves a tenant-isolation architecture.

**Required architecture**:

- Enforce tenant context in application authorization and database row-level security; include cross-tenant denial tests in CI.
- Restrict object-storage IAM to exact tenant prefixes. Do not rely on filename convention.
- Propagate tenant context through queue messages, caches, search indexes, model-registry paths, metrics, and logs. Redact record values from logs by default.
- Use envelope encryption and per-tenant data-encryption keys where the deployment requires it. Define rotation and revocation.
- Define backup retention, deletion propagation, restore isolation, incident response, RTO/RPO, data residency, and sandbox/production connection boundaries.
- Store a token, length, boolean, normalized category, or aggregate where it is enough; do not retain raw PII just because it is available.

### P1: Scale Needs A Query Planner

**Gap**: Moving from DuckDB to a warehouse or Spark does not prevent expensive scans from being generated in the first place.

**Required architecture**:

- Add a catalog-driven planner that estimates object size, selected width, relationship fan-out, API cost, sensitivity, and compute before a job starts.
- Process in tiers: metadata checks, aggregate profiles, candidate generation, then expensive relationship/ML analysis only for eligible records or cohorts.
- Separate object-level, cohort-level, and record-level work. Do not materialize a whole graph or all pairwise comparisons by default.
- Partition by org/object/date, use column pruning, and maintain schema/type, timezone, and currency normalization rules.
- Publish initial-scan, incremental-latency, scope, and on-demand-concurrency limits.

### P2: Salesforce Domain Edge Cases

Create a support matrix and tests before claiming broad compatibility:

- 15 versus 18 character IDs, deletion, merge redirects, conversion, clone behavior, and ownership changes.
- Polymorphic `WhoId`/`WhatId`, junction objects, self-lookups, hierarchy relationships, master-detail cascades, and Person Accounts.
- Multi-currency, dated exchange rates, locale/date/timezone conversion, fiscal calendars, and multi-select picklists.
- Formula, roll-up summary, encrypted, compound, geolocation, address, rich-text, and external-ID fields.
- Record types, business processes, field dependencies, global value sets, translations, custom permissions, settings, and custom metadata.
- Archived activities, history/audit objects, external/big objects, and managed-package objects.

### P2: Remediation Operations And Test Strategy

**Gap**: Findings can be reviewed but not fully owned through remediation, and a single sandbox demo is not enough proof.

**Required architecture**:

- Group repeated record-level symptoms into an underlying issue when appropriate, while retaining individual evidence.
- Capture owner, target date, business justification, exception expiry, confirmation scan, and recurrence.
- Build synthetic-corruption generators and golden orgs covering schema, automation, permissions, PII policy, and scale.
- Add formula differential tests, parser property tests, volume/fan-out load tests, and API/job interruption tests.
- Create a human-reviewed truth dataset before comparing ML approaches.

## Components To Add

```text
Policy and Coverage Service
Scan Planner and API Budget Scheduler
Snapshot Manifest and Record-State Ledger
Metadata Semantics Service and Capability Registry
Feature/Profile Version Registry
Finding Fingerprint and Lifecycle Service
Tenant Authorization and Key Management Boundary
Golden-Org Test Harness
```

## New Architecture Decisions To Ratify

1. No actionable finding exists without coverage status and immutable provenance.
2. All scans are idempotent, resumable, and manifest-backed; a watermark alone is insufficient.
3. Metadata-derived results declare semantic support; static references are not runtime violations.
4. The platform maintains a support matrix and does not claim complete Salesforce understanding.
5. Severity, confidence, and priority are distinct values with distinct UI and operating behavior.
6. Tenant isolation is a policy in storage, compute, queues, caches, logs, and backups, not only table columns.
7. Expensive ML/graph checks require scan-planner approval and follow cheaper candidate generation.
8. Every production release is tested against golden orgs and failure-recovery scenarios.

## Safer Build Order

1. Connection, policy, permission discovery, coverage contract, and scan manifest.
2. Idempotent full extraction, record-state ledger, raw metadata retention, and reconciliation scan.
3. Catalog, deterministic schema checks, profiles, and reproducible evidence.
4. Finding fingerprint/lifecycle, review workflow, exceptions, and remediation confirmation.
5. Incremental extraction with deletion and relationship invalidation.
6. Formula support matrix and static Flow/Apex dependency analysis.
7. Pattern mining and robust statistical candidates.
8. Isolation Forest and feedback ranking after baseline and evaluation controls exist.
9. Graph analysis, LLM assistance, and write-back.

## Final Assessment

The durable advantage is not a single ML model. It is a trustworthy, tenant-safe pipeline that knows what it observed, what it could not observe, why a finding exists, and how the customer proved or resolved it. Get those foundations right and ML can improve continuously without making the platform fragile.
