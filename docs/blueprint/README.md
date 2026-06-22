# Salesforce AI Data Quality Blueprint

## Purpose

This folder defines a long-term project blueprint for building a Salesforce-native discrepancy and anomaly detection platform.

The product goal is:

> Connect to any Salesforce org, understand its schema, metadata, automation, code, relationships, and historical data patterns, then identify records that are incomplete, inconsistent, unusual, or likely problematic, with human-reviewable explanations.

This is not a "single AI model trained on all Salesforce orgs." The correct product shape is a reusable platform that learns each customer org separately.

## Core Idea

Salesforce orgs are not just databases. They are living business systems made from:

- Objects and fields
- Record types
- Picklists and dependencies
- Validation rules
- Flows and workflow automation
- Apex classes and triggers
- Custom metadata, custom settings, and labels
- User permissions and ownership models
- Historical data patterns
- Cross-object relationships

The platform should combine deterministic rules, statistical profiling, graph analysis, unsupervised ML, supervised feedback learning, and LLM-assisted explanations.

## Product Thesis

The strongest version of this product is:

```text
Metadata tells us what must be true.
Historical data tells us what is usually true.
Graph relationships tell us what should be connected.
ML tells us what looks unusual.
Humans tell us what actually matters.
```

## Documentation Map

| Document | Purpose |
|---|---|
| [01-product-vision.md](01-product-vision.md) | Product vision, target users, use cases, and scope |
| [02-system-architecture.md](02-system-architecture.md) | End-to-end technical architecture |
| [03-salesforce-ingestion.md](03-salesforce-ingestion.md) | Salesforce metadata, code, config, and data extraction design |
| [04-rule-engine.md](04-rule-engine.md) | Deterministic rules from schema, validation rules, flows, and code |
| [05-ml-anomaly-detection.md](05-ml-anomaly-detection.md) | ML role, algorithms, training strategy, and scoring |
| [06-data-platform.md](06-data-platform.md) | Storage, processing, scaling, and database decisions |
| [07-security-compliance.md](07-security-compliance.md) | Security, tenant isolation, encryption, privacy, and compliance |
| [08-roadmap.md](08-roadmap.md) | MVP to enterprise roadmap |
| [09-team-learning-plan.md](09-team-learning-plan.md) | Skills, team roles, and learning curriculum |
| [10-data-models.md](10-data-models.md) | Internal platform entities and result schema |
| [11-operating-model.md](11-operating-model.md) | How the system runs per customer org |
| [12-reference-links.md](12-reference-links.md) | Official documentation and technical references |
| [13-mvp-build-plan.md](13-mvp-build-plan.md) | Concrete MVP epics, tasks, acceptance criteria, and suggested repo structure |
| [14-architecture-decisions.md](14-architecture-decisions.md) | Long-term architecture decisions and rationale |
| [15-architecture-review-and-hardening-plan.md](15-architecture-review-and-hardening-plan.md) | Architecture gaps, design gates, and hardening plan |

## Recommended MVP

Start with a focused scanner for:

- Account
- Contact
- Opportunity
- Case
- One or two custom objects

Detect:

- Missing expected fields
- Invalid field combinations
- Broken or suspicious relationships
- Unusual numeric/date values
- Lifecycle/status anomalies
- Duplicate-like records
- Records breaking strong learned patterns

Use:

- Salesforce REST API and Bulk API 2.0
- Metadata API and Tooling API
- Python, pandas, scikit-learn
- PostgreSQL or DuckDB for MVP
- Object storage/Parquet for larger datasets
- Human review workflow from day one

## Non-Goals For Version 1

- Do not try to train one global model across all customer org data.
- Do not rely on LLMs as the core anomaly detector.
- Do not attempt full Apex semantic execution in the MVP.
- Do not scan every Salesforce object before proving value on core business objects.
- Do not promise perfect accuracy. Promise explainable findings and continuous improvement.

## System North Star

The system should eventually produce findings like:

```text
Object: Opportunity
Record: 006xx00000ABC
Issue: Missing Contract__c
Confidence: 92%
Severity: High
Reason:
96% of similar Closed Won Enterprise Opportunities have Contract__c populated.
The field is referenced by Flow "Renewal Contract Creation".
The account also has active subscriptions, making this omission more suspicious.
Recommended action:
Review whether a contract should be linked or whether this is a valid exception.
```
