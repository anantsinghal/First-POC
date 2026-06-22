# North Star — what we are building and the principles that keep it sound

Plain-language source of truth. Detail lives in [`docs/blueprint/`](blueprint/README.md); current
status in [`docs/poc-vs-blueprint.md`](poc-vs-blueprint.md). This document owns the *intent*.

## 1. The goal (your vision, sharpened)

> An application that connects to a Salesforce org, learns everything about it — its objects, fields,
> relationships, rules, and automation — and then reviews **all** of the org's data to surface records
> that look wrong, incomplete, inconsistent, or unusual, with a clear human-readable reason for each,
> for a person to confirm. Built for large enterprises, with the strongest possible data security.

## 2. The one truth that decides everything

There is **no single magic AI** that "understands all Salesforce." Every org is different — different
objects, different custom fields, different rules. So the system is **one reusable engine that learns
each org separately.** It treats the org's own metadata and rules as the source of truth, learns what
that org's data normally looks like, and flags what deviates. ML is one helper inside this, not the
brain.

> Metadata tells us what *must* be true. The org's data tells us what is *usually* true.
> Relationships tell us what *should* be connected. ML tells us what looks *unusual*.
> Humans tell us what actually *matters*.

## 3. How it works, in five plain steps

1. **Learn the org.** Read the org's metadata: every object, every field (its type, whether it's
   required, its allowed picklist values), every relationship, the validation rules, flows, and code
   references. This becomes the org's "blueprint" — stored as **data**, not written into our code.
2. **Look at the data.** Measure what normal looks like: how often each field is filled, common
   value combinations, typical numbers/dates, how records relate to each other.
3. **Detect.** Run a small set of **generic checks** against that blueprint and those patterns:
   required-but-empty, value-not-in-allowed-list, broken links to parent records, missing expected
   related records, statistical outliers, "96% of similar records have X but this one doesn't," and
   genuinely unusual records (ML). None of these checks hardcode object or field names — they read
   roles from the metadata, so they work on *any* object, standard or custom.
4. **Explain & review.** Every flagged record comes with evidence and a reason in plain English. A
   person marks it: real issue / false positive / valid exception. Nothing is auto-changed.
5. **Learn from feedback.** Those human decisions make the next scan smarter and quieter for that org.

## 4. The principle that makes it last: schema is *data*, not *code*

The fatal mistake would be writing logic like *"if the object is Opportunity, check the StageName
field."* An org can have hundreds of custom objects we've never seen. So:

- **The code is generic and finite** — a handful of reusable detection engines + one rule interpreter
  that never change when a new org or a new custom object appears.
- **Everything org-specific is data** — the metadata catalog (the org's schema) and the rule catalog
  (its business rules, derived from its validation rules and human-confirmed patterns).
- **Supporting a new org, object, or relationship = ingesting more metadata, not writing more code.**
- "Per-object" survives only as a *runtime grouping* (you must compare like with like for statistics),
  and the groups are **discovered from metadata, never named in code.**

## 5. What "detect ANY anomaly" honestly means

We aim wide, but we promise **explainable candidates, not perfect verdicts.** The system reliably
covers these classes of problems and improves over time:

- Required/expected data missing · invalid picklist values · broken or missing relationships ·
  records missing their usual child records · numeric/date outliers · rare/odd field combinations ·
  records that break strong learned patterns · duplicates/near-duplicates · stale/stuck records ·
  data that contradicts the org's own validation rules, flows, or code.

It will **not** be right 100% of the time on day one — no anomaly system is. It earns trust by being
explainable and getting quieter as humans review. Honesty about what it *couldn't* see (coverage) is a
feature, not a weakness.

## 6. Security posture for enterprise (the strong version — recommended)

For a security-conscious enterprise, the strongest and most honest stance is **the customer's data
never leaves the customer's control.** Concretely:

1. **Process inside the customer's boundary.** The heavy work (reading records, profiling, scoring)
   runs in the customer's own cloud/VPC — or as close to Salesforce as possible. Our control plane
   orchestrates and stores *findings about* records; it never needs the raw records themselves. This
   is the single biggest trust lever and it must be decided **now**, because it's near-impossible to
   retrofit onto a "copy everything to the vendor" design.
2. **Collect the minimum.** Don't extract free-text/PII by default. Keep derived facts (counts, flags,
   tokens, aggregates), not raw sensitive values. Less data held = less risk.
3. **Customer-managed encryption keys.** The customer holds the key; we cannot read their data without
   it; revoking the key makes their data unreadable (instant "crypto-shred").
4. **Read-only, least-privilege.** Connected-app access scoped to exactly what's needed. Never write
   back to Salesforce automatically; any future fix is preview + approval + audit + rollback.
5. **Strict per-org isolation.** No customer's data is ever mixed with another's. Models and patterns
   are per-org. Improvements are shared at the *algorithm* level, never by sharing data.
6. **Audit everything; assume the code/metadata is untrusted input.** Log every access. Never execute
   the customer's formulas/code; parse them in a sandbox. If an AI/LLM ever reads org code or
   metadata, treat that content as data, not instructions (it could be hostile).

This posture is achievable and is what serious enterprise buyers expect (and it maps to SOC 2 / GDPR
foundations).

## 7. Where we are, and the path

- **Today:** a working, trustworthy *foundation* — coverage-aware scanning, immutable scan records,
  stable finding identities, human review, explainable findings, plus statistics + a pattern miner +
  an ML scorer. Solid, but some detectors are still hardcoded to Opportunity (the thing we're fixing).
- **Next (most important):** make detection **metadata-driven and schema-agnostic** — replace the
  hardcoded object/field detectors with generic engines that read the org's schema, and move the
  Opportunity logic into the rule catalog as *data*. This is what turns "a clever Opportunity demo"
  into "works on any org."
- **Then:** connect a real Salesforce sandbox (read-only), persist the metadata catalog, add
  relationship/graph checks, and scale extraction (Bulk API, incremental) within the security posture
  above.
- **Throughout:** make the data-residency/topology decision (point 6.1) before building multi-tenant
  internals.

## 8. Decisions to make now (so we don't pay for them later)

1. **Data residency / topology** — do we commit to "raw data stays in the customer's boundary"?
   (Strongly recommended for the enterprise + security goal.)
2. **Read-only forever, or eventual approved write-back?** (Recommend read-only first.)
3. **First target objects for proving value** — even though the engine is generic, we pick a few real
   objects to demonstrate on first.

This document is the intent. When in doubt, it wins over any single implementation detail.
