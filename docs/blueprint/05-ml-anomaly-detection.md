# ML And Anomaly Detection Design

## Core Position

ML should not be the only brain of the product.

The platform should use ML to score unusual records, but the strongest findings will come from combining:

```text
Rules + Profiles + Patterns + Graph Signals + ML Scores + Human Feedback
```

## What ML Does Here

ML helps answer:

- Does this record look unusual compared to similar records?
- Is this field combination rare?
- Is this relationship structure abnormal?
- Is this lifecycle duration unusual?
- Does this look like previous records humans confirmed as bad?

ML does not automatically know Salesforce business meaning. The system must convert Salesforce records into useful features.

## Per-Org Training Strategy

Do not train one universal model on all Salesforce orgs.

Build:

```text
One reusable platform
Many org-specific models and profiles
```

Each customer org has:

- Its own schema
- Its own custom fields
- Its own flows and Apex
- Its own business process
- Its own historical exceptions
- Its own feedback history

Therefore models should be trained per org, per object, and often per cohort.

Example:

```text
Org A
  Opportunity model
    Enterprise Closed Won cohort
    SMB Closed Won cohort
    Renewal cohort
    New Business cohort
```

## Feature Engineering

Convert records into model-readable features.

### Raw Salesforce Record

```json
{
  "Id": "006xx000001",
  "StageName": "Closed Won",
  "Amount": 125000,
  "CloseDate": "2026-06-19",
  "RecordTypeId": "012xx000001",
  "AccountId": "001xx000001",
  "Contract__c": null
}
```

### Feature Vector

```json
{
  "is_closed_won": true,
  "amount_percentile_in_cohort": 0.93,
  "days_to_close": 142,
  "has_account": true,
  "has_contract": false,
  "record_type_enterprise": true,
  "related_quote_count": 1,
  "related_case_count": 4,
  "missing_critical_field_count": 2,
  "association_rule_violations": 1
}
```

## Feature Types

| Feature Type | Examples |
|---|---|
| Boolean | has_contract, has_primary_contact, is_closed_won |
| Numeric | amount, discount percent, child record count |
| Date-derived | age in days, days in status, days to close |
| Categorical | stage, type, region, record type |
| Relationship | child counts, parent exists, graph distance |
| Rule-derived | validation violations, missing expected fields |
| Pattern-derived | association rule breaks |
| Text-derived | optional embeddings for names/descriptions |

## Algorithms To Use

### 1. Statistical Outlier Detection

Use first because it is simple and explainable.

Techniques:

- Z-score
- IQR
- Percentile thresholds
- Median absolute deviation

Good for:

- Amount outliers
- Date duration outliers
- Discount outliers
- Relationship count outliers

### 2. Isolation Forest

Use for general anomaly scoring across many features.

Good for:

- Mixed record-level outlier detection
- Medium to large datasets
- High-dimensional engineered features

Why useful:

- Works without labels
- Produces anomaly scores
- Available in scikit-learn
- Good MVP algorithm

### 3. Local Outlier Factor

Use for local neighborhood anomalies.

Good for:

- Records unusual compared with similar nearby records
- Detecting abnormal records inside specific cohorts

Concern:

- Can be heavier at large scale
- Use on sampled/cohort datasets first

### 4. Clustering

Use to group records and detect records far from clusters.

Algorithms:

- K-Means
- DBSCAN
- HDBSCAN

Good for:

- Segmenting records
- Finding natural process groups
- Detecting records that fit no group

### 5. Association Rule Mining

This is critical for the product.

Use:

- Apriori
- FP-Growth
- Support/confidence/lift

Example learned rule:

```text
IF StageName = Closed Won
AND RecordType = Enterprise
THEN Contract__c is populated
confidence = 96%
support = 31%
lift = 2.8
```

Flag records that break strong patterns.

### 6. Feedback-Trained Classification

Use after humans review enough findings.

Labels:

- Real issue
- False positive
- Valid exception
- Needs review

Algorithms:

- Logistic Regression
- Random Forest
- XGBoost/LightGBM

Purpose:

- Learn which anomalies users actually care about
- Re-rank findings
- Reduce false positives

## Training Workflow

```text
1. Extract metadata and records
2. Build object/cohort datasets
3. Generate features
4. Compute statistical profiles
5. Mine association rules
6. Train unsupervised anomaly models
7. Score all records in batches
8. Combine signals into final risk score
9. Show top findings to users
10. Collect feedback
11. Train feedback ranking model
```

## Score Composition

Final score should combine evidence:

```text
final_score =
    rule_score * 0.35
  + pattern_score * 0.25
  + ml_score * 0.20
  + graph_score * 0.15
  + code_reference_boost * 0.05
```

Weights should be configurable and eventually learned from feedback.

## Explainability

Each ML finding must be translated into understandable evidence.

Bad:

```text
Model score = 0.91
```

Good:

```text
This Opportunity is unusual because:
- It is Closed Won but has no Contract__c.
- 96% of similar Closed Won Enterprise opportunities have Contract__c.
- Its discount is in the top 2% for this cohort.
- It has no primary contact, unlike 91% of similar records.
```

## Evaluation Metrics

For unsupervised anomaly detection, evaluation is hard because labels are missing at first.

Use:

- Human review acceptance rate
- Precision at top K findings
- False positive rate by object
- Repeated suppression rate
- Admin satisfaction per finding
- Time saved in cleanup

After feedback data exists, use:

- Precision
- Recall
- F1
- ROC-AUC where applicable
- Calibration of confidence score

## Model Lifecycle

Use model tracking for:

- Dataset version
- Feature version
- Model version
- Training date
- Metrics
- Object/cohort scope
- Reviewer feedback version

MLflow is a good option for experiment tracking and model registry.

## What Not To Do Early

Do not start with:

- LLM fine-tuning
- Giant neural networks
- Autoencoders as the first approach
- One global model for all orgs
- Fully automated data fixes
- Full Apex semantic execution

Start simple, explainable, and useful.

## Long-Term ML Direction

After the MVP:

- Add graph anomaly models
- Add text embeddings for duplicate/similar records
- Add active learning for review prioritization
- Add per-user/org feedback adaptation
- Add supervised ranking model
- Add drift detection when org behavior changes

