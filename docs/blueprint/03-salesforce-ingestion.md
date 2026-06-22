# Salesforce Ingestion Design

## Goal

Build a connector that can safely extract the metadata, code/configuration, and record data required to understand one Salesforce org.

The connector must handle both small orgs and very large production orgs without trying to load everything into memory.

## Official API Surfaces

Use these Salesforce APIs:

| API | Purpose |
|---|---|
| REST API | Object discovery, describe calls, normal record access |
| Bulk API 2.0 | Large asynchronous record extraction |
| Metadata API | Retrieve metadata files and configuration |
| Tooling API | Query developer metadata such as ApexClass and ApexTrigger |
| SOQL | Object-specific queries |

Reference links are listed in [12-reference-links.md](12-reference-links.md).

## Ingestion Layers

### 1. Org Discovery

Start with global object discovery:

```text
GET /services/data/vXX.X/sobjects/
```

Collect:

- Object API name
- Label
- Custom/standard flag
- Queryable flag
- Create/update/delete flags
- Key prefixes where available

Then run sObject describe for selected objects:

```text
GET /services/data/vXX.X/sobjects/Opportunity/describe
```

Collect:

- Fields
- Field types
- Picklist values
- Required/nillable flags
- Reference targets
- Child relationships
- Record type information

### 2. Metadata Retrieval

Use Metadata API to retrieve:

- CustomObject
- CustomField
- ValidationRule
- Flow
- CustomMetadata
- CustomApplication
- Layouts where useful
- GlobalValueSet
- CustomLabels
- CustomSettings where supported
- Assignment/escalation/approval metadata where relevant

The ingestion pipeline should preserve raw metadata files and also normalize key facts into database tables.

### 3. Tooling Metadata

Use Tooling API for:

- ApexClass
- ApexTrigger
- ApexPage and components if needed
- Flow metadata where Tooling access is useful
- Symbol/reference extraction if available and reliable

MVP expectation:

- Extract Apex/trigger body text
- Parse object and field references heuristically
- Store references and code locations
- Do not claim full semantic execution

### 4. Record Data Extraction

For large datasets, use Bulk API 2.0 query jobs.

Object extraction strategy:

```text
For each selected object:
    1. Generate SOQL query for selected fields
    2. Start Bulk API query job
    3. Poll job status
    4. Download results in chunks
    5. Store raw rows
    6. Normalize into analysis format
```

Field selection should include:

- Id
- CreatedDate
- LastModifiedDate
- SystemModstamp
- OwnerId
- RecordTypeId
- All non-sensitive configured fields
- Lookup/master-detail fields
- Status/stage fields
- Business-critical custom fields

## Sensitive Data Handling

Do not extract every field blindly in production SaaS mode.

Create field inclusion policies:

| Field Type | Default |
|---|---|
| IDs and lookup fields | Include |
| Picklists/status fields | Include |
| Dates and numbers | Include |
| Boolean flags | Include |
| Text fields | Include selectively |
| Long text/rich text | Exclude by default |
| Email/phone/name | Tokenize or include only with permission |
| Encrypted fields | Exclude unless explicitly allowed |

## Incremental Sync

For repeat scans, avoid full reloads.

Use:

- `SystemModstamp`
- `LastModifiedDate`
- Object-specific watermarks
- Bulk API filtered queries

Example:

```sql
SELECT Id, StageName, Amount, CloseDate, AccountId, LastModifiedDate
FROM Opportunity
WHERE SystemModstamp >= 2026-06-18T00:00:00Z
```

## Data Quality During Ingestion

The ingestion pipeline itself should record:

- Query start/end time
- API version
- Objects scanned
- Fields scanned
- Rows extracted
- Failed objects
- Failed fields
- Permission errors
- API limit usage
- Skipped fields and reason

## Metadata Normalization

Normalize metadata into internal structures:

```text
Org
ObjectDefinition
FieldDefinition
RelationshipDefinition
RecordTypeDefinition
ValidationRuleDefinition
FlowDefinition
ApexReference
CustomMetadataRule
```

Raw metadata should also be retained for audit and reprocessing.

## Extraction Risk

| Risk | Mitigation |
|---|---|
| API limits | Bulk jobs, backoff, scheduling, object prioritization |
| Huge objects | Partition queries, incremental sync, warehouse storage |
| Field-level security | Respect permissions and report inaccessible fields |
| Sensitive data | Field policy, masking, tokenization, tenant controls |
| Metadata complexity | Store raw files and add support iteratively |
| Deleted records | Decide whether recycle bin/deleted data matters by use case |

