# Phase 21 — Format validation (email / phone / url), schema-driven

Add a metadata-driven `FormatValidityDetector` that flags values whose FORMAT doesn't match what the
field's Salesforce type declares — malformed emails, phones, and URLs. Uses the field `type` from
`describe` (email/phone/url), so it's generic and hardcodes nothing. ADDITIVE and schema-driven: it
no-ops when there's no schema, so the CSV demo and all existing tests are unchanged.

## 1. Domain — extend `app/domain/schema.py`
- `FieldType`: add `EMAIL = "email"`, `PHONE = "phone"`, `URL = "url"`.
- `FieldDescriptor`: add `length: int | None = None` (declared max length; captured now, used by a
  future length check).
- `schema_from_salesforce_describe`: map `email`→EMAIL, `phone`→PHONE, `url`→URL (add these BEFORE the
  generic string fallback); set `length = field.get("length")` when present (else None).
- `schema_from_dict`: parse the new types via the existing `FieldType(...)` path and read `length`.
- Add a helper to `ObjectSchema`: `format_fields() -> list[FieldDescriptor]` returning fields whose
  `data_type in {EMAIL, PHONE, URL}`.

## 2. Detector — `app/detection/schema_driven/format_validity.py` — `class FormatValidityDetector`
- `id: str = "invalid_format"`; `requirement: ClassVar[CheckRequirement | None] = None`.
- Module-level compiled heuristics (documented as pragmatic, not RFC-exhaustive):
  - email: `re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")`
  - url: `re.compile(r"^https?://[^\s.]+\.[^\s]+$", re.IGNORECASE)`
  - phone: valid if, after removing `+ - ( ) . space`, the remainder is all digits with length 7–15.
- `detect(ctx)`: if `ctx.schema is None` return `[]`. For each `fd in ctx.schema.format_fields()` where
  `fd.name in ctx.fields`, for each record with non-empty Id and non-empty value, validate by
  `fd.data_type`; if it fails, emit candidate:
  - title `f"Invalid {fd.name} format"`, field_name `fd.name`,
    evidence `{"field": fd.name, "data_type": fd.data_type.value, "observed_value": value,
    "why": f"{fd.name} value '{value}' does not look like a valid {fd.data_type.value}."}`.
  - (Empty values are a required/completeness concern, not a format concern — skip them.)
- `make_candidate(...)`; import `clean` from `app.domain.values`, `FieldType` from `app.domain.schema`,
  `re`.

## 3. Policy — `app/policy/field_policy.py`
Add `"invalid_format": (Severity.MEDIUM, 0.90, 0.75)`.

## 4. Registry — `app/detection/registry.py`
APPEND `FormatValidityDetector()` (no-ops without schema → demo/existing tests unchanged).

## 5. Tests — `tests/test_format_validity.py` (NEW)
- Build a schema (via `schema_from_dict`) for "Contact" with `Email` (data_type email), `Phone`
  (phone), `Website` (url). Records: valid + invalid for each (e.g. "a@b.com" valid / "not-an-email"
  invalid; "+1 (415) 555-1234" valid / "12" invalid; "https://x.com" valid / "x.com" invalid). Assert
  `FormatValidityDetector().detect(ctx)` flags exactly the invalid ones, all `finding_type=="invalid_format"`,
  empty values NOT flagged.
- `schema_from_salesforce_describe` maps an `email`/`phone`/`url` field to the right `FieldType` and
  captures `length`.
- No-op: `ctx.schema is None` → `[]`.
- Demo regression: 11-row demo via `build_default_registry().detect(ctx_without_schema)` unchanged
  count; full registry on the demo yields zero `invalid_format`.

## Constraints
- `from __future__ import annotations`; type hints; docstrings. detection/domain stay framework-free.
- Additive; demo (11-row) finding count unchanged (invalid_format = 0 on the demo, which has no
  schema and no email/phone/url fields).

## Done criteria
- `python -m pytest -q` green; demo finding count unchanged. Report files + results and confirm
  invalid_format fires on malformed email/phone/url under a schema but no-ops on the demo.
