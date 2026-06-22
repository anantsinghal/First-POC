# Implementation Roadmap

## Roadmap Philosophy

Do not start by trying to understand every Salesforce org completely.

Start with a narrow, useful scanner. Then add metadata depth, ML depth, graph depth, and automation understanding over time.

## Phase 0: Research And Prototype

Duration: 2-4 weeks

Goals:

- Validate Salesforce API access
- Extract metadata for a dev org
- Extract sample records
- Build simple data profiler
- Generate first anomaly report

Deliverables:

- Salesforce auth prototype
- Object/field describe extractor
- Bulk data extractor for 2-3 objects
- Field completeness report
- Simple outlier report

Success:

```text
Given Account, Contact, Opportunity CSV/API data,
the system produces useful missing-field and outlier reports.
```

## Phase 1: MVP Scanner

Duration: 8-12 weeks

Objects:

- Account
- Contact
- Opportunity
- Case
- 1-2 custom objects

Capabilities:

- OAuth Salesforce connection
- Metadata ingestion
- Bulk record ingestion
- Rule engine v1
- Data profiler
- Association rule mining
- Isolation Forest scoring
- Findings table
- Basic review UI

Findings:

- Missing expected fields
- Rare field combinations
- Relationship gaps
- Numeric/date outliers
- Records breaking strong patterns

Success:

```text
An admin can connect a sandbox and receive explainable,
reviewable findings within a day.
```

## Phase 2: Metadata-Aware Intelligence

Duration: 3-4 months

Capabilities:

- Validation rule parsing v1
- Flow field reference extraction
- Apex field reference extraction
- Record type aware profiling
- Custom metadata ingestion
- Rule source attribution
- Severity/confidence scoring

Success:

```text
Findings are tied to Salesforce metadata, flows,
validation rules, or code references wherever possible.
```

## Phase 3: Relationship Graph Engine

Duration: 2-3 months

Capabilities:

- Cross-object relationship graph
- Child count profiling
- Orphan/missing-child detection
- Process chain detection
- Graph-based severity signals

Example:

```text
Closed Won Opportunity -> expected Quote -> expected Contract -> expected Order
```

Success:

```text
The system detects missing or abnormal business process links,
not just bad fields on one object.
```

## Phase 4: Feedback Learning

Duration: 2-3 months

Capabilities:

- Human review labels
- Feedback-based ranking model
- Exception management
- Suppression rules
- Precision-at-top-K reporting
- Model versioning

Success:

```text
The system becomes less noisy and more accurate inside each org over time.
```

## Phase 5: Enterprise Scale

Duration: 4-6 months

Capabilities:

- Multi-tenant SaaS architecture
- Object storage/Parquet data lake
- Warehouse/lakehouse support
- Incremental sync
- Job orchestration
- Observability
- Tenant-level security controls
- Compliance readiness

Success:

```text
The system can process large production orgs with millions
or tens of millions of records reliably.
```

## Phase 6: AI Assistant And Copilot

Duration: 3-4 months

Capabilities:

- Explain findings in natural language
- Ask questions about org data quality
- Summarize Apex/Flow dependencies
- Suggest cleanup plans
- Generate admin-reviewable rules
- RAG over org metadata and findings

Success:

```text
Admins can ask "Why was this flagged?" and receive
evidence-backed answers grounded in metadata and data.
```

## Phase 7: Write-Back And Workflow

Duration: 2-4 months

Capabilities:

- Create cleanup tasks in Salesforce
- Create custom Data_Quality_Finding__c records
- Assign findings to owners
- Approval workflow for fixes
- Dry-run correction suggestions
- Audit log for write-back

Success:

```text
Findings become operational cleanup workflows,
not just reports.
```

## Recommended Build Order

1. Data extractor
2. Metadata catalog
3. Data profiler
4. Rule engine
5. Pattern miner
6. ML scorer
7. Findings store
8. Review UI
9. Feedback learning
10. Graph engine
11. LLM explanation
12. Enterprise scaling

## Biggest Technical Risks

| Risk | Mitigation |
|---|---|
| Too many false positives | Start with high-confidence findings, add feedback loop |
| Salesforce API limits | Bulk API, incremental sync, scan scheduling |
| Metadata complexity | Iterative parser support, raw metadata retention |
| Apex semantic complexity | Start with static references, not full execution |
| Sensitive data concerns | Field policies, masking, tenant isolation |
| Huge data volume | Batch processing, Parquet, warehouse/lakehouse |
| Weak explanations | Evidence-based findings, not opaque model scores |

