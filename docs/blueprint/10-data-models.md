# Internal Data Models

## Purpose

This document defines the main entities the platform should store internally.

Names are illustrative and can be adapted to the actual stack.

## Tenant And Org

### tenant

```sql
tenant_id
name
plan
created_at
status
```

### salesforce_org

```sql
org_id
tenant_id
salesforce_org_id
instance_url
api_version
environment_type
connected_at
last_successful_sync_at
status
```

## Metadata Catalog

### object_definition

```sql
object_id
tenant_id
org_id
api_name
label
is_custom
is_queryable
is_createable
is_updateable
is_deleted
last_described_at
```

### field_definition

```sql
field_id
tenant_id
org_id
object_id
api_name
label
data_type
is_custom
is_nillable
is_unique
is_external_id
is_encrypted
reference_to
picklist_values_json
is_sensitive
extraction_policy
```

### relationship_definition

```sql
relationship_id
tenant_id
org_id
parent_object
child_object
field_api_name
relationship_name
relationship_type
```

### record_type_definition

```sql
record_type_id
tenant_id
org_id
object_api_name
salesforce_record_type_id
developer_name
label
is_active
```

## Rule Catalog

### rule_definition

```sql
rule_id
tenant_id
org_id
object_api_name
source_type
source_name
severity
confidence
condition_json
expectation_json
explanation_template
is_active
created_at
```

Source types:

```text
schema
validation_rule
flow
apex
custom_metadata
learned_pattern
admin_custom
```

### code_reference

```sql
reference_id
tenant_id
org_id
source_type
source_name
object_api_name
field_api_name
operation_type
line_number
confidence
raw_snippet
```

Operation types:

```text
read
write
query
condition
unknown
```

## Data Profiles

### object_profile

```sql
profile_id
tenant_id
org_id
object_api_name
record_count
scan_id
computed_at
profile_json
```

### field_profile

```sql
field_profile_id
tenant_id
org_id
object_api_name
field_api_name
record_count
null_count
null_rate
distinct_count
top_values_json
numeric_stats_json
date_stats_json
computed_at
```

### cohort_profile

```sql
cohort_profile_id
tenant_id
org_id
object_api_name
cohort_definition_json
record_count
profile_json
computed_at
```

Example cohort:

```json
{
  "object": "Opportunity",
  "filters": {
    "StageName": "Closed Won",
    "RecordType": "Enterprise"
  }
}
```

## Pattern Mining

### learned_pattern

```sql
pattern_id
tenant_id
org_id
object_api_name
cohort_definition_json
antecedent_json
consequent_json
support
confidence
lift
status
created_at
```

Status:

```text
candidate
active
admin_confirmed
suppressed
rejected
```

## ML Models

### model_artifact

```sql
model_id
tenant_id
org_id
object_api_name
cohort_definition_json
algorithm
feature_version
training_dataset_uri
artifact_uri
metrics_json
trained_at
status
```

### record_score

```sql
score_id
tenant_id
org_id
scan_id
object_api_name
record_id
rule_score
pattern_score
ml_score
graph_score
final_score
score_components_json
scored_at
```

## Findings

### finding

```sql
finding_id
tenant_id
org_id
scan_id
object_api_name
record_id
finding_type
title
severity
confidence
final_score
status
explanation
evidence_json
suggested_action
created_at
updated_at
```

Finding types:

```text
schema_violation
validation_rule_violation
missing_expected_field
relationship_gap
numeric_outlier
date_outlier
rare_combination
pattern_break
ml_anomaly
duplicate_candidate
code_dependency_risk
```

### finding_evidence

```sql
evidence_id
finding_id
source_type
source_name
field_api_name
evidence_text
evidence_json
confidence
```

### finding_feedback

```sql
feedback_id
finding_id
tenant_id
org_id
reviewer_user_id
label
comment
created_at
```

Labels:

```text
real_issue
false_positive
valid_exception
needs_business_review
duplicate_finding
```

## Scan Jobs

### scan_job

```sql
scan_id
tenant_id
org_id
scan_type
status
requested_by
started_at
completed_at
objects_included_json
rows_processed
findings_created
errors_json
```

Scan types:

```text
initial_full
scheduled_full
incremental
targeted_object
metadata_only
rescore_only
```

