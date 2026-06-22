# Rule Engine Design

## Purpose

The rule engine handles discrepancies that can be derived from explicit metadata, configuration, or business logic references.

This is the most trustworthy part of the system because it does not depend on probabilistic ML.

## Rule Sources

| Source | Example Rule |
|---|---|
| Schema | Field is required or must match type |
| Picklist metadata | Value must be in allowed set |
| Lookup relationship | Parent record should exist |
| Record type | Field expectations differ by record type |
| Validation rule | Formula defines invalid state |
| Flow decision | Flow assumes field exists in a process |
| Apex reference | Code reads or writes a field |
| Custom metadata | Config defines allowed process behavior |
| Learned pattern promoted to rule | Admin confirms a discovered pattern as required |

## Rule Categories

### 1. Schema Rules

Examples:

- Required field is blank
- Lookup field references missing parent
- Picklist value is invalid
- Date field has impossible value
- Field is populated with value inconsistent with type expectations

### 2. Conditional Rules

Example:

```text
IF Opportunity.StageName = "Closed Won"
THEN Opportunity.CloseDate must not be null
```

### 3. Relationship Rules

Example:

```text
IF Account.Type = "Customer"
THEN Account should have at least one active Contact
```

### 4. Lifecycle Rules

Example:

```text
IF Case.Status = "Escalated"
THEN Escalation_Date__c should be populated
```

### 5. Automation Dependency Rules

Example:

```text
Flow "Renewal Contract Creation" references Opportunity.Contract__c.
Closed Won renewal opportunities missing Contract__c should be higher severity.
```

## Internal Rule Representation

Represent rules in a portable JSON format:

```json
{
  "rule_id": "opp_closed_won_contract_required",
  "org_id": "org_123",
  "object_api_name": "Opportunity",
  "source_type": "validation_rule",
  "source_name": "Closed_Won_Requires_Contract",
  "severity": "high",
  "condition": {
    "all": [
      { "field": "StageName", "operator": "equals", "value": "Closed Won" }
    ]
  },
  "expectation": {
    "field": "Contract__c",
    "operator": "not_null"
  },
  "explanation_template": "Closed Won opportunities should have Contract__c populated."
}
```

## Rule Execution Model

The engine should process records in batches:

```text
Load object records
Load active rules for object
For each batch:
    Evaluate conditions
    Evaluate expectations
    Emit findings
Store findings with evidence
```

For scale, translate simple rules into SQL where possible.

Example:

```sql
SELECT Id
FROM Opportunity
WHERE StageName = 'Closed Won'
AND Contract__c IS NULL;
```

## Validation Rule Parsing

Salesforce validation rules use formulas. Full formula support is a large project.

Recommended stages:

| Stage | Support |
|---|---|
| V1 | Common formula functions and operators |
| V2 | More formula functions, picklists, date logic |
| V3 | Full parser with test fixtures |
| V4 | Rule simulation against sampled records |

Start with:

- `AND`
- `OR`
- `NOT`
- `ISBLANK`
- `ISPICKVAL`
- `TEXT`
- `ISNEW` as unsupported/deferred for historical scans
- Equality/inequality
- Date comparisons

## Flow Analysis

For flows, the first goal is not full execution.

Start by extracting:

- Object references
- Field references
- Decision conditions
- Assignment/update targets
- Triggering object
- Status/stage logic

Use this to increase confidence and severity when data breaks assumptions.

## Apex Analysis

Full Apex interpretation is hard. Do not start there.

Start with static analysis:

- Extract object names
- Extract field references
- Extract SOQL queries
- Extract DML targets
- Identify trigger object
- Identify before/after insert/update/delete contexts

Example evidence:

```text
Field Contract__c is referenced in Apex class RenewalService and trigger OpportunityTrigger.
```

## Rule Confidence

Rules should have a confidence score by source.

| Source | Default Confidence |
|---|---|
| Required schema | 1.00 |
| Active validation rule | 0.95 |
| Admin-confirmed custom rule | 0.95 |
| Flow decision reference | 0.75 |
| Apex field reference | 0.65 |
| Learned pattern | 0.60 to 0.95 depending on support/confidence |

## Findings From Rules

Each rule finding should include:

- Object API name
- Record Id
- Rule Id
- Source metadata
- Severity
- Confidence
- Field(s) involved
- Explanation
- Suggested action

## Long-Term Goal

The rule engine should become a metadata-to-check compiler:

```text
Salesforce metadata/config/code
        ↓
Normalized rule catalog
        ↓
SQL/batch-executable checks
        ↓
Explainable findings
```

