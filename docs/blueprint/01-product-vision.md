# Product Vision

## Problem

Salesforce orgs accumulate large volumes of operational data over many years. As orgs evolve, data becomes inconsistent with business processes, automation, code expectations, and hidden operational patterns.

Common issues include:

- Required business data missing even when Salesforce does not enforce it
- Records stuck in stale lifecycle states
- Parent records missing expected child records
- Closed Won deals without contracts, quotes, billing records, or key contacts
- Cases missing entitlement or SLA context
- Custom object records that no longer follow process expectations
- Duplicate or near-duplicate records
- Data combinations that are technically valid but operationally wrong
- Records that break assumptions inside Apex, Flows, reports, or integrations

Traditional Salesforce data quality tools usually find explicit issues. They struggle with implicit business patterns and cross-object process context.

## Product Mission

Build a Salesforce-native intelligence engine that identifies data discrepancies using:

- Explicit Salesforce metadata and configuration
- Business logic from validation rules, flows, and Apex references
- Historical org-specific data patterns
- Cross-object relationship graphs
- Statistical and ML-based anomaly scoring
- Human review and feedback loops

## Target Users

| User | Need |
|---|---|
| Salesforce Admin | Find bad data without manually writing every rule |
| Salesforce Architect | Understand data/process drift across objects |
| Data Quality Team | Prioritize records needing cleanup |
| RevOps / SalesOps | Improve pipeline, quote, contract, and account data |
| Support Ops | Detect Case, entitlement, SLA, and lifecycle inconsistencies |
| Implementation Partner | Audit customer orgs during discovery and migration |
| Enterprise IT | Monitor production data quality continuously |

## Product Positioning

Possible product categories:

- Salesforce data quality copilot
- Salesforce org discrepancy scanner
- Metadata-aware anomaly detection platform
- Business logic aware data observability for Salesforce
- AI-assisted Salesforce data audit platform

The strongest positioning is:

> A Salesforce-aware data quality intelligence platform that combines metadata, automation, code, historical patterns, and ML to find explainable business data issues.

## Key Differentiator

Most anomaly detection tools operate on tables. This product should understand Salesforce as a business system.

That means it should know:

- Which fields belong to which objects
- Which objects are related
- Which record types change expected behavior
- Which flows and Apex logic reference a field
- Which child records usually exist for a business process
- Which exceptions are normal for this customer org

## Product Principles

1. **Explainability first**

   Every finding must say why it was flagged.

2. **Org-specific learning**

   Each org gets its own metadata model, patterns, and feedback history.

3. **Human-in-the-loop**

   Users decide whether a finding is real, a valid exception, or a false positive.

4. **Metadata before ML**

   Use schema, rules, and automation wherever possible. ML should support, not replace, deterministic reasoning.

5. **Scalable by design**

   Huge orgs must be processed with batch jobs, incremental scans, and analytical storage.

6. **No unsafe automatic fixes**

   The system can recommend fixes, but automated updates should require explicit configuration and approval.

## Use Cases

### Sales Data Quality

- Closed Won opportunities missing quote, contract, legal entity, or billing account
- Enterprise opportunities missing primary contacts
- Discounts outside normal ranges for segment, product, or region
- Renewals with missing prior contract/subscription links

### Service Data Quality

- Cases missing entitlement, SLA, product, or contact information
- Cases stuck in status beyond normal lifecycle time
- Priority cases without escalation records
- Reopened cases with missing reason codes

### Master Data Quality

- Accounts without expected contacts
- Contacts missing role, email, or account relationship
- Duplicate accounts or contacts
- Inactive accounts with active opportunities or contracts

### Custom Process Quality

- Custom project records missing milestones
- Onboarding records without tasks
- Subscription records missing entitlement or billing links
- Custom metadata values that conflict with record behavior

### Implementation Audit

- Discover undocumented business rules
- Identify objects with poor data completeness
- Find automation-dependent fields
- Generate cleanup backlog before migration or go-live

## Success Metrics

| Metric | Target |
|---|---|
| True positive rate | Improve over time per org |
| Review acceptance rate | High confidence findings should be accepted often |
| False positive rate | Reduce through feedback and exception handling |
| Time to first findings | Under 24 hours for MVP orgs |
| Scan scalability | Millions of records via batch processing |
| Explanation quality | Admin can understand why each issue was flagged |
| Metadata coverage | Increasing support for validation rules, flows, Apex, custom metadata |

## Product Risk

The biggest risk is not model training. It is trust.

Users will abandon the product if it floods them with noisy, unexplained anomalies. The product must prioritize clear, high-confidence issues first, then expand coverage gradually.

