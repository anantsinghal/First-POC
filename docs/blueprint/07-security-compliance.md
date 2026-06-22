# Security And Compliance Architecture

## Security Principle

This product handles sensitive customer CRM data. Security must be designed from the beginning, not added later.

The safest product posture is:

```text
Collect minimum necessary data.
Keep each org isolated.
Encrypt everything.
Log access.
Explain every finding.
Never train across customers without explicit consent.
```

## Data Access

Use OAuth-based Salesforce connections.

Recommended:

- Least-privilege connected app scopes
- Admin-controlled object/field access
- Explicit permission review during onboarding
- Ability to exclude sensitive objects/fields
- Read-only access for MVP

Avoid:

- Username/password flows
- Hardcoded tokens
- Broad write permissions
- Automatic data updates without approval

## Tenant Isolation

Every customer tenant should have:

- Separate org connection configuration
- Separate encryption context
- Separate raw data partitions
- Separate metadata catalog
- Separate models/profiles
- Separate feedback history

Logical isolation is required for SaaS. Enterprise customers may require physical isolation or private deployment.

## Encryption

Use:

- TLS in transit
- Encryption at rest for databases
- Encryption at rest for object storage
- Secret manager for tokens
- Optional customer-managed keys for enterprise

Secrets:

- Salesforce refresh tokens
- API credentials
- Database credentials
- Encryption keys

Store only in a managed secret vault.

## Sensitive Field Controls

The system should classify fields before extraction.

Potential sensitive fields:

- Email
- Phone
- Name
- Address
- Free text
- Notes
- Description
- Long text
- Attachments/files
- Encrypted fields
- Health/financial identifiers

Default field policy:

| Field Category | Default Action |
|---|---|
| Ids/lookups | Include |
| Picklists/status | Include |
| Dates/numbers/booleans | Include |
| Names/emails/phones | Mask/tokenize or require permission |
| Long text/free text | Exclude by default |
| Files/attachments | Exclude from MVP |
| Encrypted fields | Exclude by default |

## Privacy-Preserving ML

Do not train a global model directly on customer record data.

Safer options:

- Per-org models only
- Shared algorithm improvements without shared data
- Opt-in anonymized telemetry
- Federated learning only if there is a strong future need
- Aggregated benchmark patterns without raw values

## Access Control

Product roles:

| Role | Permissions |
|---|---|
| Tenant Admin | Manage Salesforce connection, field policy, users |
| Data Quality Manager | Review findings, configure rules |
| Reviewer | Review assigned findings |
| Viewer | Read-only dashboards |
| System Operator | Operational logs, no customer data by default |

## Audit Logging

Log:

- User login
- Salesforce connection creation/update
- Object/field extraction policy changes
- Scan job start/end
- Finding review actions
- Data export actions
- Rule changes
- Model deployment changes

## Compliance Readiness

Potential future requirements:

- SOC 2
- GDPR
- CCPA/CPRA
- HIPAA only if explicitly targeting health data
- Data residency requirements
- Enterprise DPA support

Build early foundations:

- Data deletion per tenant
- Exportable audit logs
- Data retention controls
- Field exclusion controls
- Subprocessor inventory
- Security incident process

## LLM Safety

If using LLMs:

- Do not send sensitive raw data unless explicitly permitted
- Prefer metadata/code explanations over full record payloads
- Mask or redact PII
- Log prompts safely
- Use tenant-specific retrieval boundaries
- Do not allow generated rules to become active without review

## Write-Back Safety

MVP should be read-only.

Later write-back options:

- Create Salesforce task
- Create custom finding record
- Add record comment/chatter post
- Export CSV cleanup backlog
- Suggest field updates

Any automatic data correction must require:

- Explicit customer configuration
- Dry run preview
- Approval workflow
- Rollback strategy
- Audit trail

