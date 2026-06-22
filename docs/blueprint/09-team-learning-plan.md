# Team And Learning Plan

## Skill Areas Needed

This product sits at the intersection of:

- Salesforce architecture
- Data engineering
- Machine learning
- Backend engineering
- Security/compliance
- Product design
- MLOps

## Minimum Founding Team

| Role | Responsibility |
|---|---|
| Salesforce Architect | Metadata, flows, Apex, org behavior, product correctness |
| Data/ML Engineer | Profiling, anomaly models, feature engineering |
| Backend Engineer | APIs, jobs, storage, multi-tenancy |
| Frontend/Product Engineer | Review UI and workflows |
| Security Advisor | OAuth, tenant isolation, compliance posture |

One strong engineer can prototype the MVP, but a serious product needs the above skills.

## Learning Curriculum

### 1. Python Data Engineering

Learn:

- Python fundamentals
- pandas
- NumPy
- file processing
- API calls
- batch processing
- error handling
- Parquet

Outcome:

```text
Can process Salesforce-like datasets with hundreds of thousands
or millions of records.
```

### 2. SQL And Data Profiling

Learn:

- Joins
- Group by
- Window functions
- Aggregations
- Null analysis
- Duplicate detection
- Cohort analysis

Outcome:

```text
Can write queries to detect missing data, unusual counts,
and cross-object inconsistencies.
```

### 3. Salesforce Platform Internals

Learn:

- Standard/custom objects
- Fields and field types
- Record types
- Validation rules
- Flows
- Apex triggers/classes
- Custom metadata/settings
- SOQL
- REST API
- Bulk API 2.0
- Metadata API
- Tooling API

Outcome:

```text
Can extract and interpret org metadata and business logic references.
```

### 4. Statistics

Learn:

- Mean, median, mode
- Standard deviation
- Z-score
- IQR
- Percentiles
- Correlation
- Conditional probability
- Support/confidence/lift

Outcome:

```text
Can define what normal means for an object/cohort.
```

### 5. Practical ML

Learn:

- Feature engineering
- Categorical encoding
- Train/test split
- Overfitting
- Precision/recall/F1
- Unsupervised vs supervised learning
- Isolation Forest
- Local Outlier Factor
- K-Means/DBSCAN
- Association rule mining
- Random Forest/XGBoost for feedback models

Outcome:

```text
Can build anomaly scoring models that support, rather than replace,
metadata-aware reasoning.
```

### 6. Graph Analysis

Learn:

- Nodes and edges
- Parent-child relationships
- Orphan detection
- Relationship counts
- Traversal
- Community detection basics

Outcome:

```text
Can model Salesforce records as business process graphs.
```

### 7. LLM/RAG Basics

Learn:

- Prompting
- Embeddings
- Retrieval augmented generation
- Grounded explanations
- Redaction and privacy
- Hallucination control

Outcome:

```text
Can use LLMs to explain metadata/code/findings safely,
without making them the core detector.
```

### 8. System Design

Learn:

- Batch processing
- Job queues
- Idempotency
- Multi-tenancy
- Data lakes
- Warehouses
- APIs
- Observability
- Rate limit handling

Outcome:

```text
Can design a scanner that handles real production data volume.
```

## Practice Projects

### Project 1: CSV Profiler

Input:

- Account.csv
- Contact.csv
- Opportunity.csv

Output:

- Field completeness report
- Duplicate report
- Relationship report
- Numeric/date outlier report

### Project 2: Salesforce Metadata Extractor

Input:

- Salesforce dev org

Output:

- Object catalog JSON
- Field catalog JSON
- Relationship map
- Record type map

### Project 3: Rule Engine

Input:

- Metadata JSON
- Sample records

Output:

- Rule findings
- Severity
- Explanation

### Project 4: Pattern Miner

Input:

- Opportunity records

Output:

- Association rules
- Pattern break findings

### Project 5: ML Scorer

Input:

- Feature table

Output:

- Isolation Forest anomaly scores
- Top suspicious records
- Human-readable reasons

### Project 6: End-To-End MVP

Input:

- One Salesforce sandbox

Output:

- Findings UI/report
- Human review labels
- Feedback-informed ranking

