# Operating Model

## Per-Org Lifecycle

Every Salesforce org should go through a controlled lifecycle:

```text
Connect
    ↓
Discover
    ↓
Configure
    ↓
Extract
    ↓
Profile
    ↓
Score
    ↓
Review
    ↓
Learn
    ↓
Monitor
```

## 1. Connect

Tasks:

- Admin authorizes Salesforce connected app
- Platform stores OAuth credentials securely
- Initial permission check runs
- System reports accessible objects and fields

Output:

```text
Connection is active and read-only scan can begin.
```

## 2. Discover

Tasks:

- Retrieve global object list
- Describe selected objects
- Retrieve metadata
- Query code/automation references
- Build org catalog

Output:

```text
The platform understands the org's available objects,
fields, relationships, and core metadata.
```

## 3. Configure

Tasks:

- Select objects to scan
- Exclude sensitive fields
- Select scan schedule
- Configure severity thresholds
- Define known valid exceptions
- Choose whether to store raw data and retention period

Output:

```text
Org scan policy is approved.
```

## 4. Extract

Tasks:

- Run Bulk API jobs
- Store raw data
- Normalize records
- Record extraction metrics
- Handle failed fields/objects

Output:

```text
Analysis-ready data exists for selected objects.
```

## 5. Profile

Tasks:

- Compute object profiles
- Compute field profiles
- Compute cohort profiles
- Compute relationship profiles
- Mine initial patterns

Output:

```text
The platform has a baseline for what normal looks like.
```

## 6. Score

Tasks:

- Run deterministic rules
- Run pattern break detection
- Run graph checks
- Run ML anomaly scoring
- Combine scores
- Create ranked findings

Output:

```text
Findings are generated with confidence, severity, and explanation.
```

## 7. Review

Tasks:

- Users review findings
- Users mark outcomes
- Users add comments
- Users suppress known exceptions
- Users promote useful patterns to rules

Output:

```text
The system learns what matters to this org.
```

## 8. Learn

Tasks:

- Re-rank findings using feedback
- Update exception logic
- Train feedback classifier when enough labels exist
- Monitor false positives
- Update confidence thresholds

Output:

```text
The system becomes more accurate over time.
```

## 9. Monitor

Tasks:

- Run scheduled scans
- Detect metadata changes
- Detect data drift
- Alert on quality regressions
- Track cleanup progress

Output:

```text
Data quality becomes continuous, not a one-time audit.
```

## Scan Cadence

Recommended defaults:

| Scan Type | Cadence |
|---|---|
| Metadata scan | Daily or when requested |
| Incremental data scan | Daily |
| Full deep scan | Weekly or monthly |
| High-risk object scan | Daily or hourly if needed |
| Model retraining | Weekly/monthly or after enough feedback |

## Finding Lifecycle

```text
New
    ↓
Reviewed
    ↓
Confirmed Issue / False Positive / Valid Exception
    ↓
Assigned / Suppressed / Converted To Rule
    ↓
Resolved / Archived
```

## Human Review Labels

| Label | Meaning |
|---|---|
| Real Issue | Finding is valid and should be fixed |
| False Positive | System was wrong |
| Valid Exception | Unusual but acceptable |
| Needs Business Review | Requires process owner decision |
| Duplicate Finding | Same underlying issue already exists |

## Operational Dashboards

### Admin Dashboard

- Total findings
- Findings by severity
- Findings by object
- Review status
- Top recurring issues
- Cleanup progress

### Data Quality Dashboard

- Field completeness trends
- Object quality score
- Pattern break trends
- Relationship health
- False positive trends

### Engineering Dashboard

- Scan job health
- API usage
- Extraction failures
- Model training status
- Processing latency

## Quality Score

Create an org/object quality score using:

```text
Completeness
Rule pass rate
Relationship health
Anomaly density
Review-confirmed issue rate
Trend direction
```

Do not hide raw findings behind a score. The score is only a summary.

## Long-Term Operating Model

The platform should mature from:

```text
Audit tool
```

to:

```text
Continuous Salesforce data quality observability platform
```

