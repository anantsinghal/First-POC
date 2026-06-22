# MVP Build Plan

## MVP Objective

Build a working Salesforce data quality scanner that can:

1. Connect to one Salesforce sandbox or dev org.
2. Extract metadata and records for selected objects.
3. Build profiles and learned patterns.
4. Detect rule, relationship, statistical, and ML anomalies.
5. Show explainable findings for human review.

## MVP Scope

Objects:

- Account
- Contact
- Opportunity
- Case
- One configurable custom object

Detection types:

- Missing expected fields
- Invalid picklist or schema issues
- Missing lookup/parent relationships
- Missing expected child records
- Numeric/date outliers
- Rare field combinations
- Association rule breaks
- Isolation Forest anomaly score

Out of scope:

- Automatic record fixes
- Full Apex execution
- Full Flow execution
- Global cross-org ML training
- Attachments/files analysis
- Real-time scanning

## Suggested Tech Stack

### Backend

- Python
- FastAPI
- PostgreSQL
- SQLAlchemy or SQLModel
- Celery/RQ for background jobs
- Redis for queue broker

### Data/ML

- pandas
- DuckDB
- PyArrow/Parquet
- scikit-learn
- mlxtend for association rules

### Frontend

- React or Next.js
- Findings table
- Finding detail drawer
- Feedback actions

### Infrastructure

- Docker Compose for local MVP
- Object storage later
- MLflow optional after first ML experiments

## Suggested Repository Structure

```text
salesforce-ai-data-quality/
  apps/
    api/
    web/
    worker/
  packages/
    sf_connector/
    metadata_catalog/
    rule_engine/
    profiler/
    anomaly_engine/
    explanation_engine/
  migrations/
  notebooks/
  docs/
  tests/
  docker-compose.yml
  README.md
```

## Epics

### Epic 1: Salesforce Connection

Tasks:

- Create connected app setup guide
- Implement OAuth flow
- Store encrypted credentials
- Test API connection
- Fetch org identity/basic metadata

Acceptance criteria:

```text
An admin can connect a sandbox and the backend can call Salesforce APIs.
```

### Epic 2: Metadata Catalog

Tasks:

- Call Describe Global
- Run sObject Describe for selected objects
- Store object definitions
- Store field definitions
- Store relationships
- Store record types

Acceptance criteria:

```text
The platform can show selected objects, fields, field types,
picklist values, and relationships from the connected org.
```

### Epic 3: Data Extraction

Tasks:

- Generate SOQL queries for selected objects
- Run Bulk API 2.0 query jobs
- Download result chunks
- Store raw records as Parquet
- Store extraction metrics

Acceptance criteria:

```text
The platform extracts records for Account, Contact, Opportunity,
Case, and one custom object without manual CSV upload.
```

### Epic 4: Data Profiler

Tasks:

- Compute field null rates
- Compute distinct counts
- Compute top values
- Compute numeric/date stats
- Compute relationship counts
- Compute record type/cohort profiles

Acceptance criteria:

```text
For each object, the system produces a profile report
showing completeness, distributions, and relationship health.
```

### Epic 5: Rule Engine V1

Tasks:

- Implement schema required checks
- Implement picklist validity checks
- Implement lookup existence checks
- Implement simple conditional rules
- Store findings

Acceptance criteria:

```text
The system creates explainable findings from deterministic checks.
```

### Epic 6: Pattern Miner

Tasks:

- Convert records into categorical itemsets
- Run association rule mining
- Filter by support/confidence/lift
- Detect pattern-breaking records
- Store learned patterns

Acceptance criteria:

```text
The system can discover patterns such as
"Closed Won Enterprise opportunities usually have Contract__c"
and flag records that break strong patterns.
```

### Epic 7: ML Anomaly Scorer

Tasks:

- Build feature generator
- Encode categorical fields
- Train Isolation Forest per object/cohort
- Score records in batches
- Store record scores

Acceptance criteria:

```text
Each scanned record receives an anomaly score,
and high-scoring records are turned into findings with explanations.
```

### Epic 8: Findings UI

Tasks:

- Build findings table
- Add filters by object/severity/confidence/type
- Build finding detail view
- Show evidence
- Add review labels

Acceptance criteria:

```text
Users can review findings and mark them as real issue,
false positive, valid exception, or needs business review.
```

### Epic 9: Feedback Loop V1

Tasks:

- Store user feedback
- Suppress repeated false positives
- Boost admin-confirmed patterns
- Track acceptance rate

Acceptance criteria:

```text
The system uses feedback to reduce repeated noise
and prioritize confirmed issue types.
```

## MVP Milestones

| Milestone | Outcome |
|---|---|
| M1 | Connect to Salesforce and retrieve metadata |
| M2 | Extract selected object records |
| M3 | Produce profiling report |
| M4 | Generate deterministic findings |
| M5 | Generate pattern/ML findings |
| M6 | Review findings in UI |
| M7 | Feedback affects future ranking |

## MVP Demo Script

1. Connect Salesforce sandbox.
2. Select Account, Contact, Opportunity, Case.
3. Run initial scan.
4. Open dashboard.
5. Show field completeness and relationship profile.
6. Open Opportunity findings.
7. Show a Closed Won opportunity missing Contract__c.
8. Explain evidence from pattern/profile/rule/code reference where available.
9. Mark finding as real issue.
10. Show feedback stored and used for future prioritization.

## MVP Definition Of Done

The MVP is done when:

- It works against a real Salesforce sandbox.
- It processes at least 100k records across selected objects.
- It produces at least four finding types.
- Findings include evidence and explanations.
- Users can review and label findings.
- Feedback is persisted.
- All scan jobs are logged.
- The system can be rerun without corrupting previous results.

