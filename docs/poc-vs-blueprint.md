# POC ↔ Blueprint: where this repo sits, and what's next

This repo (`First POC`) is the **trustworthy-foundation slice** of the full product blueprint in
[`docs/blueprint/`](blueprint/README.md). The blueprint describes a Salesforce-native, metadata-aware,
per-org data-quality / anomaly platform. The blueprint's own hardening plan
([15-architecture-review-and-hardening-plan.md](blueprint/15-architecture-review-and-hardening-plan.md))
is explicit that the **foundation comes before ML** — its "Safer Build Order" puts Isolation Forest at
step 8 of 9, *after* the coverage contract, manifest, profiling, and review loop exist. That is exactly
what this POC built.

> Blueprint thesis: *Metadata = what must be true · data = what is usually true · graph = what should be
> connected · ML = what looks unusual · humans = what actually matters.* The POC covers the first, parts of
> the second, and the human loop. Pattern-mining and ML are the next slices.

## Component map (blueprint doc 02 → this repo)

| Blueprint component | Status | Where in this repo |
|---|---|---|
| Salesforce Connector (REST/Describe/Tooling/Metadata SOAP) | ✅ built — bounded ≤2000 records, **no Bulk API 2.0** | `app/salesforce/` |
| Data Ingestion (CSV + SOQL) | ✅ built — **no incremental `SystemModstamp` sync** | `app/ingestion/` |
| Metadata Ingestion / Org Catalog | ⚠️ partial — describe used transiently for field selection; **not persisted** | `app/ingestion/salesforce_source.py` |
| Code / Automation Ingestion (Apex, Flow, validation rules) | ❌ not built (only `verify` probes) | — |
| Rule Engine (deterministic) | ✅ built — 8 rule detectors | `app/detection/rules/` |
| Data Profiler | ✅ built | `app/profiling/` |
| Statistical candidates (IQR, MAD, rare-combination) | ✅ built | `app/detection/statistical/` |
| **Pattern Miner (association rules: support/confidence/lift)** | ✅ **built** — `pattern_break` with base-rate margin guard | `app/detection/patterns/` |
| Graph Analyzer (orphans, missing children) | ❌ not built (`missing_account` is a single-field proxy) | — |
| **ML Anomaly Scorer (Isolation Forest)** | ✅ **built** — `ml_anomaly`, reproducible + explainable (per-object; cohorts pending) | `app/detection/ml/` |
| Explanation Engine | ✅ built — `why` + evidence on every finding | `app/detection/base.py` (`make_candidate`) |
| Findings Store + Review UI + feedback | ✅ built | `app/persistence/`, `app/static/`, `app/api/` |

## Design-gate status (blueprint doc 15)

| Gate | Status in POC |
|---|---|
| 1. Coverage contract (included/skipped/inaccessible with reasons) | ✅ **strong** — `app/coverage/` grade + data-driven suppressed checks; "missing access is a coverage failure" |
| 2. Reproducible snapshot (immutable versions) | ⚠️ partial — scan manifest has `source_hash`, `policy_version`, `parser_version`, fields; **no metadata/feature/model versions** |
| 3. Incremental correctness (deletes/merges/reparenting) | ❌ not built — no record-state ledger, no Bulk/change feed |
| 4. Runtime boundary (static ≠ runtime; `semantic_status`) | ⚠️ honored in spirit ("candidates, not verdicts"); no formula parser / rule semantic labels |
| 5. Tenant isolation | ❌ single-tenant local |
| 6. Golden-org tests | ❌ unit tests + httpx MockTransport only |
| Severity / confidence / priority kept distinct (decision 5) | ✅ stored as three separate fields |

## What "do we have ML?" honestly means here

**Now: partially.** The Pattern Miner (`pattern_break`, association-rule mining) and the ML Anomaly
Scorer (`ml_anomaly`, Isolation Forest — reproducible and explainable) are built and registered. What
remains for the full vision is **schema-agnostic generalization**: today's detectors still know about
specific objects/fields (e.g. the Opportunity rules), whereas the product must be driven entirely by
the org's metadata so it works on any object — see [`north-star.md`](north-star.md) §4. The labeled
reviewer feedback and stable finding fingerprints are the training-data foundation for the supervised
re-ranking layer that comes later.

## Recommended next slices (additive — each is a new detector Strategy in the existing registry)

Following the blueprint's safer build order, on top of today's foundation:

1. **Epic 6 — Pattern Miner** (`app/detection/patterns/`): mine association rules
   (support/confidence/lift) per object/record-type; flag records that break strong rules with
   explainable evidence (e.g. *"96% of Closed Won Enterprise have Contract__c; this one doesn't"*).
   Dependency: `pandas` + `mlxtend` (or a small hand-rolled FP-style miner). Lowest risk, highest
   explainability — do this first.
2. **Epic 7 — Isolation Forest anomaly scorer** (`app/detection/ml/`): build per-object feature vectors
   (booleans, numerics, date-derived, relationship counts, rule/pattern violations), train an
   unsupervised model per object/cohort, emit a calibrated anomaly score as a finding with
   evidence. Dependency: `scikit-learn`. Guardrails from doc 15: exclude tiny cohorts, temporal
   holdout, treat history as possibly-contaminated.
3. Later: Graph Analyzer → Code/Flow/Apex ingestion (`semantic_status`) → feedback-trained re-ranking →
   multi-tenant / scan-planner / Bulk API + incremental ledger.

Each plugs into `build_default_registry()` with a `severity_for` row and a `CheckRequirement` — no
engine/service/API change (Open/Closed). See [CLAUDE.md](../CLAUDE.md) "Adding a new anomaly check."
