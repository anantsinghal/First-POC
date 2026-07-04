# Universal Strategy — from Salesforce scanner to a universal data-anomaly platform

This is the permanent v2+ vision. It extends [`north-star.md`](north-star.md) (the Salesforce
product) to the general goal: **find data anomalies accurately, on the basis of patterns and ML,
in any system that holds governed data.** Detail on the Salesforce blueprint stays in
[`blueprint/`](blueprint/README.md); scale posture in [`scalability.md`](scalability.md).

## 1. The one big idea: contract vs. behavior

Every serious data system has two layers:

```
Contract  — what the data MUST look like.
            Salesforce: metadata (describe, required flags, picklists, relationships,
                        validation rules, flows).
            Database:   schema (types, NOT NULL, CHECK, foreign keys, enums).
            API:        JSON Schema / OpenAPI spec.

Behavior  — what the data ACTUALLY looks like: the records themselves,
            and the patterns hiding in them.
```

**An anomaly is a gap between contract and behavior** — either an explicit violation of the
contract, or a deviation from behavior so consistent it amounts to an unwritten rule.

Because *every* governed data system has a contract, an engine built on this abstraction is not a
Salesforce tool. It is a **contract-vs-behavior engine** with pluggable translators.

## 2. Three classes of anomaly, three kinds of intelligence

| Class | Example | How it's caught | Confidence |
|---|---|---|---|
| Contract violation | required field empty; value not in picklist; dangling reference | deterministic checks generated FROM the contract | ~certain |
| Pattern violation | 96% of Closed-Won Enterprise deals have a contract; this one doesn't | statistics + association-rule mining over the data | high, quantified |
| Statistical oddity | record unlike its peers across many fields at once | unsupervised ML (e.g. Isolation Forest), per cohort | candidate only |

## 3. Where ML honestly fits

Naively "training a model on the data" fails for three reasons: the history already contains the
errors (the model learns bad data as normal), there are no labels on day one, and unexplained
scores are ignored by reviewers. The winning shape is a **layered system with a learning flywheel**:

```
Contract rules   →  what MUST be true          (day-one, explainable)
Data profiling   →  what's USUALLY true         (statistics, pattern mining)
Unsupervised ML  →  what looks UNUSUAL          (cohort-relative outlier scoring)
Human review     →  what actually MATTERS       (real issue / false positive / valid exception)
Supervised ML    →  TRAINED ON THOSE LABELS     (per-org "will a human care?" ranking model)
```

Every review label is a training example. Accumulated labels — not the algorithm — are the moat.
There is deliberately **no universal pretrained model across customers**: every org/database has
different fields and norms, and enterprise data cannot be pooled. One reusable engine; separately
learned per dataset. That is a privacy feature, not a limitation.

## 4. Universal architecture: one brain, many plugs

```
 Salesforce ─┐
 PostgreSQL ─┤   ADAPTERS                 CANONICAL MODEL               ONE ENGINE
 Snowflake ──┼─► read the system's ─────► entities, fields, types, ───► rules · profiling ·
 MySQL ──────┤   contract + stream        required, allowed values,     patterns · ML · graph ·
 JSON APIs ──┘   its records              relationships, rules          reconcile · explain
                                                                            │
                                        findings + review UI + feedback ◄──┘
                                        quality scores + audit + retention
```

- **Adapter** (per system): reads the contract (describe / information_schema / JSON Schema) and
  streams records. Translates into the canonical model. ~Weeks of work per new system.
- **Canonical model** (the heart): one internal vocabulary for "entity X has field Y of type Z,
  required?, allowed values?, references entity W" + a portable rule format for conditional logic.
- **Engine** (written once): all detection families, reconciliation, coverage honesty, planner,
  explanation, review workflow, metrics/quality, audit/retention. Never knows the source system.

Mapping examples for future adapters:

| Canonical concept | Salesforce | PostgreSQL | JSON API |
|---|---|---|---|
| Entity | sObject | table | object type |
| Required | `nillable=false` | `NOT NULL` | `required: []` |
| Allowed values | picklist values | `CHECK IN (...)` / enum | `enum: []` |
| Reference | lookup `referenceTo` | foreign key | `$ref` |
| Conditional rule | validation rule | trigger/constraint | business spec |

## 5. Phased plan

1. **Prove it on Salesforce** — the hardest, richest contract. ✅ done (v1, this repo).
2. **Run on real org data** (carhuborg): tune precision, accumulate the first human labels. ← next
3. **Extract the universal core + second adapter**: move the Salesforce-describe translator into
   the adapter layer, publish the canonical model as the seam, add a **PostgreSQL adapter**
   (schema is trivially readable). Success on two very different systems proves the abstraction.
4. **The learning flywheel**: train the per-dataset supervised ranking model from accumulated
   labels; add drift detection (alert when behavior shifts).
5. **Platform**: adapter SDK; per-deployment enterprise isolation; more sources.

## 6. Non-negotiables carried from v1

Explainability on every finding · human-in-the-loop, never auto-fix · coverage honesty (a sampled
scan never claims completeness) · data stays in the customer's boundary · per-dataset learning,
no cross-customer data pooling.

---

## 7. Audit — does the current codebase already follow this? (verified 2026-06-30)

**Yes — the v1 codebase is this strategy, implemented for its first adapter.** Verified against the
code, not from memory:

| Strategy element | Status in code | Evidence |
|---|---|---|
| One generic engine, zero source knowledge | ✅ | `grep -ri salesforce` over `app/detection/ app/profiling/ app/coverage/ app/planning/` finds **only two comments** (an Id-format caveat, a docstring word) — no imports, no logic. The engine cannot tell what system fed it. |
| Canonical contract model | ✅ | `app/domain/schema.py`: `FieldType/FieldDescriptor/ObjectSchema` — generic (string/number/date/boolean/reference/picklist/email/phone/url), imports nothing Salesforce. Proven on a never-before-seen custom object. |
| Adapter translates contract → canonical | ✅ (×2) | `app/ingestion/salesforce_source.py` (describe → schema) and CSV/multi-CSV adapters; all implement one `DataSource` protocol. |
| Contract-derived deterministic checks | ✅ | schema-driven detectors (required/picklist/reference/format) generated from the canonical model. |
| Conditional rules as portable data | ✅ | `app/policy/rules/*.json` + one generic interpreter. |
| Pattern mining + cohort ML + graph + reconcile | ✅ | association `pattern_break`, cohort-aware Isolation Forest, orphan/missing-children, equivalence-group reconciliation. |
| Feedback captured as first-class data | ✅ | `feedback` table, suppression across re-scans, acceptance/false-positive metrics, quality score + trend. |
| Honest coverage, audit, retention, boundary deployment | ✅ | sampled-scan downgrade, audit log, purge, single-tenant in-boundary design. |
| **Supervised model trained on labels** | ✅ phase 56 - training pipeline EXISTS (`LearningService`: cold-start-guarded logistic regression, JSON-persisted, heuristic fallback); still awaiting real label volume to activate in production | `app/services/learning_service.py` + `app/learning/features.py`; ranking uses the model once enough labels accumulate, else falls back to the phase-4 heuristic unchanged. |
| **Second adapter (database)** | ✅ (SQLite; PostgreSQL = same shape via information_schema, listed next) | the `DataSource` seam proved by SqliteTableSource. |
| Translator placement | ✅ (moved to adapter) | `schema_from_salesforce_describe` now lives in `app/ingestion/salesforce_schema.py`. |

**Verdict:** nothing in v1 has to be undone to become the universal platform. The remaining work is
additive: run on real data (2), extract the seam + add a DB adapter (3), train on labels (4).
