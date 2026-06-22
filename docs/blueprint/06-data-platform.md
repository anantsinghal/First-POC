# Data Platform And Storage Architecture

## Core Requirement

The platform must support both small orgs and huge production orgs.

The system should not load all records into memory. It should process records in batches, store raw extracts, compute profiles incrementally, and persist only useful derived facts and findings.

## Recommended Storage Layers

```text
Salesforce
    ↓
Raw Landing Zone
    ↓
Normalized Analytical Store
    ↓
Feature Store / Profiles
    ↓
Findings Store
    ↓
Review UI / API
```

## MVP Architecture

For a first working product:

| Need | Technology |
|---|---|
| Application database | PostgreSQL |
| Local analytical processing | DuckDB or pandas |
| Raw files | Parquet files |
| ML | scikit-learn |
| Background jobs | Celery, RQ, or simple scheduled workers |
| UI | React/Next.js or Salesforce Lightning app later |

MVP storage:

```text
PostgreSQL:
- org metadata
- scan configuration
- profiles
- findings
- feedback

Parquet:
- raw extracted records
- feature datasets
```

## Enterprise Architecture

For large customers:

| Need | Technology Options |
|---|---|
| Raw data lake | S3, Azure Blob, GCS |
| Large analytics | Snowflake, BigQuery, Databricks, Redshift |
| Batch processing | Spark, Databricks, Ray, Dask |
| Metadata/results DB | PostgreSQL |
| Search | OpenSearch/Elasticsearch |
| Vector search | pgvector or managed vector DB |
| Workflow orchestration | Airflow, Dagster, Prefect |
| Model tracking | MLflow |

## Database Choice

### PostgreSQL

Use for:

- Customers/orgs
- Object catalog
- Field catalog
- Rule catalog
- Scan jobs
- Findings
- Feedback
- User/team permissions
- Lightweight vector search with pgvector

Why:

- Reliable relational model
- Great for SaaS product state
- Strong transactions
- Works well for multi-tenant metadata

### Parquet + Object Storage

Use for:

- Raw Salesforce extracts
- Large object snapshots
- Feature datasets
- Model training datasets

Why:

- Efficient columnar storage
- Cheap for large data
- Good for analytical jobs
- Works with DuckDB, Spark, Databricks, Snowflake external tables

### Warehouse/Lakehouse

Use for:

- Very large org analysis
- Complex analytical scans
- Historical trend analysis
- Cross-object aggregations

Good options:

- Snowflake
- BigQuery
- Databricks
- Redshift

### Graph Database

Optional at first.

Use when relationship analysis becomes deep:

- Neo4j
- PostgreSQL graph-style tables
- Warehouse graph queries

MVP can model graph relationships in relational tables.

### Vector Database

Optional at first.

Use for:

- Similar field names
- Similar records
- Duplicate detection
- RAG over metadata/code docs

MVP can use pgvector inside PostgreSQL.

## Multi-Tenant Design

Every major table should include:

```text
tenant_id
org_id
```

Raw files should be partitioned by:

```text
tenant_id/org_id/object_api_name/extract_date/
```

Never mix customer data for model training unless customers explicitly opt into privacy-preserving shared learning.

## Data Processing Patterns

### Full Scan

Run when:

- First org onboarding
- Major metadata change
- Periodic deep audit

### Incremental Scan

Run daily/hourly depending on need:

- Extract records changed since last watermark
- Recompute affected profiles
- Rescore changed records
- Re-evaluate related records

### Targeted Scan

Run when:

- User selects object
- User adds custom rule
- New validation rule/flow is detected
- Specific data cleanup project starts

## Scaling Strategy

| Challenge | Approach |
|---|---|
| Millions of records | Bulk API + chunked processing |
| Wide objects | Field selection policy |
| API limits | Backoff, scheduling, object priority |
| Large joins | Warehouse/lakehouse processing |
| Model scoring | Batch inference |
| High review volume | Ranking and deduplication |
| Repeated false positives | Exception and feedback suppression |

## Data Retention

Recommended:

- Raw extracts: configurable, often 30-90 days
- Profiles: keep historical trend
- Findings: keep until archived/resolved
- Feedback: keep long term
- Model artifacts: keep versioned history

## Observability

Track:

- Scan duration
- Rows processed
- API calls used
- Failed objects
- Extraction errors
- Model training duration
- Findings generated
- Findings accepted/rejected
- False positive trends
- Object-level coverage

## Recommended Initial Choice

For the first serious implementation:

```text
PostgreSQL
DuckDB
Parquet
Python workers
scikit-learn
MLflow optional
```

This is enough to prove the product without overbuilding.

