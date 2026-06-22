# Architecture Decisions

## ADR 001: Per-Org Models Instead Of One Global Model

Decision:

Use per-org models and profiles. Do not train one universal model on all customer data.

Rationale:

- Salesforce orgs are heavily customized.
- Customer data cannot usually be mixed for privacy/compliance reasons.
- A pattern that is anomalous in one org may be normal in another.

Consequence:

- The platform must support repeated training and profiling per org.
- Shared improvements should happen at algorithm/template level, not raw-data level.

## ADR 002: Batch-First Scanning

Decision:

Build batch and incremental scans before real-time scanning.

Rationale:

- Production Salesforce orgs can be huge.
- Bulk API and analytical processing are better suited to large scans.
- Most data quality issues do not require millisecond detection.

Consequence:

- MVP focuses on scheduled scans and on-demand scans.
- Real-time checks can be added later for critical objects.

## ADR 003: Metadata Before ML

Decision:

Use Salesforce metadata, schema, validation rules, and automation references as the first layer of intelligence.

Rationale:

- Explicit metadata is more trustworthy than inferred ML patterns.
- Explainability is essential for user trust.
- ML should support scoring and discovery, not invent unsupported truth.

Consequence:

- The metadata catalog and rule engine are core platform components.

## ADR 004: Read-Only MVP

Decision:

The MVP must not update Salesforce data automatically.

Rationale:

- Findings may be probabilistic.
- Customer trust requires safe review first.
- Write-back creates compliance and rollback complexity.

Consequence:

- MVP outputs reports/findings/review actions only.
- Write-back workflows are introduced later with approvals and audit trails.

## ADR 005: Store Raw Metadata, Normalize Key Facts

Decision:

Preserve raw Salesforce metadata files/responses and also normalize important facts into database tables.

Rationale:

- Salesforce metadata is complex and support will grow over time.
- Raw data enables reprocessing when parsers improve.
- Normalized data enables fast rule/profile/scoring operations.

Consequence:

- Storage design needs both raw artifact storage and relational metadata catalog.

## ADR 006: Explainable Findings Over Opaque Scores

Decision:

Every finding must include evidence and a human-readable reason.

Rationale:

- Admins need to trust and act on findings.
- Opaque ML scores will cause rejection.
- Evidence enables review, audit, and feedback.

Consequence:

- The scoring engine must preserve components, not just final scores.

## ADR 007: PostgreSQL First, Lakehouse Later

Decision:

Use PostgreSQL plus Parquet/DuckDB for MVP. Add warehouse/lakehouse support for enterprise scale.

Rationale:

- PostgreSQL is enough for product metadata, findings, feedback, and early profiles.
- Parquet gives efficient raw/feature storage.
- DuckDB enables powerful local analytics without enterprise infrastructure.

Consequence:

- The MVP remains simple.
- The data layer should be abstracted enough to add Snowflake/BigQuery/Databricks later.

## ADR 008: Human Feedback Is A Core Data Asset

Decision:

Reviewer labels are first-class training and ranking data.

Rationale:

- Initial anomaly detection will have false positives.
- Org-specific exceptions are common.
- Feedback is how the system becomes trusted over time.

Consequence:

- Review UI and feedback schema are part of MVP, not optional extras.

## ADR 009: LLMs Are Assistive, Not Authoritative

Decision:

Use LLMs for summarization, explanation, code/metadata assistance, and admin-facing reasoning. Do not use them as the sole anomaly detector.

Rationale:

- LLMs can hallucinate.
- Deterministic checks and statistical evidence are more auditable.
- Sensitive data must be controlled carefully.

Consequence:

- LLM outputs should be grounded in stored evidence.
- Generated rules require human approval before activation.

## ADR 010: Configurable Field Extraction Policy

Decision:

Customers must control which fields are extracted and stored.

Rationale:

- CRM data can include PII and regulated data.
- Some customers will not allow free text or encrypted fields to leave Salesforce.
- Less data reduces risk.

Consequence:

- Ingestion must support object/field allowlists, denylists, and masking.

