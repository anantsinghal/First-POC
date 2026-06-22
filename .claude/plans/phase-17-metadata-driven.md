# Phase 17 — Metadata-driven schema layer + generic detector engines

The product must work on ANY org without hardcoding object/field names (see `docs/north-star.md` §4).
This phase adds the reusable layer: a **schema model** (the org's field roles as data) and **generic
detector engines** that read those roles instead of hardcoding names. ADDITIVE: the engines no-op when
no schema is present, so the CSV demo and all existing tests are unaffected. In Salesforce mode the
schema is built from `describe`; the engines then activate automatically.

Do NOT modify `tests/test_scanner.py`, `tests/test_detection.py`, `tests/test_new_detectors.py`,
`tests/test_pattern_miner.py`, `tests/test_ml_anomaly.py`. Do NOT change existing detector behavior.

## 1. Domain — `app/domain/schema.py`
- `class FieldType(str, Enum)`: STRING, NUMBER, DATE, BOOLEAN, REFERENCE, PICKLIST, ID, OTHER.
- `@dataclass(frozen=True) class FieldDescriptor`: `name: str`, `data_type: FieldType`,
  `required: bool = False`, `picklist_values: frozenset[str] | None = None`,
  `reference_to: tuple[str, ...] = ()`, `is_name_field: bool = False`, `is_unique: bool = False`.
- `@dataclass(frozen=True) class ObjectSchema`: `object_name: str`, `fields: dict[str, FieldDescriptor]`.
  Methods: `get(name) -> FieldDescriptor | None`; `required_fields() -> list[FieldDescriptor]`
  (required and data_type != ID); `picklist_fields() -> list[FieldDescriptor]` (data_type==PICKLIST and
  picklist_values truthy); `reference_fields() -> list[FieldDescriptor]` (data_type==REFERENCE).
- `def schema_from_salesforce_describe(describe: dict[str, Any]) -> ObjectSchema`: map each field in
  `describe.get("fields", [])`:
  - type map: `id`→ID, `reference`→REFERENCE, `picklist`/`multipicklist`→PICKLIST, `boolean`→BOOLEAN,
    `date`/`datetime`/`time`→DATE, `double`/`int`/`currency`/`percent`→NUMBER, else STRING.
  - `required = (not field.get("nillable", True)) and data_type != FieldType.ID`.
  - `picklist_values = frozenset(p["value"] for p in field.get("picklistValues", []) if p.get("active",
    True))` when PICKLIST else None.
  - `reference_to = tuple(field.get("referenceTo") or [])`; `is_name_field = bool(field.get("nameField"))`;
    `is_unique = bool(field.get("unique"))`. object_name from `describe.get("name")`.
- `def schema_from_dict(data: dict[str, Any]) -> ObjectSchema`: load a simplified sidecar/config format
  (object_name + list of fields with the above attributes) so a schema can be supplied as DATA even
  without a live org (used by tests now; CSV sidecar later).

## 2. DetectionContext — `app/detection/base.py`
Add field `schema: ObjectSchema | None = None` to `DetectionContext` (after `profile`). No other change.
(`make_candidate` untouched.)

## 3. Ingestion — carry the schema
- `app/ingestion/base.py`: add `schema: "ObjectSchema | None" = None` to `LoadedDataset` (optional, last
  field; import under `TYPE_CHECKING` or directly from `app.domain.schema`).
- `app/ingestion/salesforce_source.py`: after `describe = await client.describe_sobject(...)`, build
  `schema = schema_from_salesforce_describe(describe)` and pass it into the returned `LoadedDataset`.
- `app/ingestion/csv_source.py`: `schema=None` (unchanged behavior).

## 4. Service — `app/services/scan_service.py`
When building the `DetectionContext`, pass `schema=dataset.schema` (the only change). Coverage/profile
logic unchanged.

## 5. Generic engines — `app/detection/schema_driven/` (`__init__.py` empty)
Each returns `[]` immediately if `ctx.schema is None`. Per-record detectors skip records with empty Id.
- `required_field.py` — `RequiredFieldDetector` (`id="required_missing"`,
  `requirement: ClassVar[CheckRequirement | None] = None`): for each `fd in ctx.schema.required_fields()`
  where `fd.name in ctx.fields` AND `fd.data_type != FieldType.REFERENCE` (references handled below),
  flag records where `clean(record.get(fd.name))` is empty. evidence `{"rule": "schema-required
  (metadata)", "field": fd.name, "data_type": fd.data_type.value, "observed_value": None, "why":
  f"{fd.name} is required by the org metadata but is empty."}`; title `f"Required field {fd.name} is
  empty"`; field_name `fd.name`.
- `picklist_validity.py` — `PicklistValidityDetector` (`id="picklist_invalid"`): for each `fd in
  ctx.schema.picklist_fields()` where `fd.name in ctx.fields`, flag records where value is non-empty and
  `value not in fd.picklist_values`. evidence `{"field": fd.name, "observed_value": value,
  "allowed_values": sorted(fd.picklist_values)[:50], "why": f"{fd.name}={value} is not an active picklist
  value in the org metadata."}`; title `f"Invalid {fd.name} value"`; field_name `fd.name`.
- `referential_integrity.py` — `ReferentialIntegrityDetector` (`id="reference_missing"`): for each `fd in
  ctx.schema.reference_fields()` where `fd.name in ctx.fields` AND `fd.required`, flag records where the
  reference is empty. evidence `{"field": fd.name, "reference_to": list(fd.reference_to), "observed_value":
  None, "why": f"Required lookup {fd.name} (to {', '.join(fd.reference_to) or 'parent'}) is empty."}`;
  title `f"Missing required reference {fd.name}"`; field_name `fd.name`.
- All use `make_candidate(...)` and import `clean` from `app.domain.values`, `FieldType` from
  `app.domain.schema`.

## 6. Policy — `app/policy/field_policy.py`
Add: `"required_missing": (Severity.HIGH, 0.95, 0.90)`, `"picklist_invalid": (Severity.HIGH, 0.97, 0.93)`,
`"reference_missing": (Severity.HIGH, 0.93, 0.88)`.

## 7. Registry — `app/detection/registry.py`
APPEND `RequiredFieldDetector()`, `PicklistValidityDetector()`, `ReferentialIntegrityDetector()`.
(They no-op without a schema, so the demo/existing tests are unchanged.)

## 8. Tests — `tests/test_schema_driven.py` (NEW)
- `schema_from_salesforce_describe`: feed a sample describe dict (a picklist field `StageName` with
  active values, a required text field, a required `reference` `AccountId` with `referenceTo:["Account"]`,
  an `id` field) → assert the ObjectSchema has correct types/required/picklist_values/reference_to.
- Generic detectors with a synthetic `ObjectSchema` + records: assert `RequiredFieldDetector` flags the
  empty-required rows; `PicklistValidityDetector` flags an out-of-set StageName; `ReferentialIntegrityDetector`
  flags the empty required AccountId; and ALL THREE return `[]` when `ctx.schema is None`.
- **Wiring test**: build a fake `DataSource` whose `load()` returns a `LoadedDataset` with a schema +
  records, run it through `ScanService.run_scan` (use a tmp `Database`), and assert the resulting findings
  include `picklist_invalid` / `required_missing` (proves schema flows source → context → engines).
- Regression: the 11-row CSV demo via `build_default_registry().detect(ctx_without_schema)` still yields
  the same count (the three new engines no-op without a schema).

## Constraints
- `from __future__ import annotations`; type hints; docstrings. detection/domain stay framework-free.
- Engines are pure additive; no change to existing detectors or their finding types.

## Done criteria
- `python -m pytest -q` green; 11-row demo finding count unchanged. Report files + results, and confirm
  the three engines no-op when schema is absent.
