from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class FieldType(str, Enum):
    """Supported field types derived from Salesforce describe (and sidecar)."""
    STRING = "string"
    NUMBER = "number"
    DATE = "date"
    BOOLEAN = "boolean"
    REFERENCE = "reference"
    PICKLIST = "picklist"
    ID = "id"
    OTHER = "other"


@dataclass(frozen=True)
class FieldDescriptor:
    """Describes one field and its role for generic checks."""
    name: str
    data_type: FieldType
    required: bool = False
    picklist_values: frozenset[str] | None = None
    reference_to: tuple[str, ...] = ()
    is_name_field: bool = False
    is_unique: bool = False


@dataclass(frozen=True)
class ObjectSchema:
    """Schema for an object: fields with their roles."""
    object_name: str
    fields: dict[str, FieldDescriptor]

    def get(self, name: str) -> FieldDescriptor | None:
        return self.fields.get(name)

    def required_fields(self) -> list[FieldDescriptor]:
        return [fd for fd in self.fields.values()
                if fd.required and fd.data_type != FieldType.ID]

    def picklist_fields(self) -> list[FieldDescriptor]:
        return [fd for fd in self.fields.values()
                if fd.data_type == FieldType.PICKLIST and fd.picklist_values]

    def reference_fields(self) -> list[FieldDescriptor]:
        return [fd for fd in self.fields.values()
                if fd.data_type == FieldType.REFERENCE]


def schema_from_salesforce_describe(describe: dict[str, Any]) -> ObjectSchema:
    """Build ObjectSchema from a Salesforce /describe response."""
    object_name = describe.get("name") or "Unknown"
    fields: dict[str, FieldDescriptor] = {}
    for field in describe.get("fields", []):
        ftype = (field.get("type") or "").lower()
        if ftype == "id":
            data_type = FieldType.ID
        elif ftype == "reference":
            data_type = FieldType.REFERENCE
        elif ftype in ("picklist", "multipicklist"):
            data_type = FieldType.PICKLIST
        elif ftype == "boolean":
            data_type = FieldType.BOOLEAN
        elif ftype in ("date", "datetime", "time"):
            data_type = FieldType.DATE
        elif ftype in ("double", "int", "currency", "percent"):
            data_type = FieldType.NUMBER
        else:
            data_type = FieldType.STRING

        required = (not field.get("nillable", True)) and data_type != FieldType.ID

        if data_type == FieldType.PICKLIST:
            pvals = [
                p["value"] for p in field.get("picklistValues", [])
                if p.get("active", True) and p.get("value")
            ]
            picklist_values: frozenset[str] | None = frozenset(pvals)
        else:
            picklist_values = None

        reference_to = tuple(field.get("referenceTo") or [])
        is_name_field = bool(field.get("nameField"))
        is_unique = bool(field.get("unique"))

        fd = FieldDescriptor(
            name=field.get("name", ""),
            data_type=data_type,
            required=required,
            picklist_values=picklist_values,
            reference_to=reference_to,
            is_name_field=is_name_field,
            is_unique=is_unique,
        )
        if fd.name:
            fields[fd.name] = fd

    return ObjectSchema(object_name=object_name, fields=fields)


def schema_from_dict(data: dict[str, Any]) -> ObjectSchema:
    """Load a simplified schema dict (for tests / sidecar)."""
    object_name = data.get("object_name") or "Unknown"
    fields: dict[str, FieldDescriptor] = {}
    for f in data.get("fields", []):
        dtype = f.get("data_type", "string")
        try:
            data_type = FieldType(dtype.lower())
        except ValueError:
            data_type = FieldType.OTHER

        pvals = f.get("picklist_values")
        picklist_values = frozenset(pvals) if pvals else None
        ref_to = tuple(f.get("reference_to") or [])
        fd = FieldDescriptor(
            name=f.get("name", ""),
            data_type=data_type,
            required=bool(f.get("required", False)),
            picklist_values=picklist_values,
            reference_to=ref_to,
            is_name_field=bool(f.get("is_name_field", False)),
            is_unique=bool(f.get("is_unique", False)),
        )
        if fd.name:
            fields[fd.name] = fd
    return ObjectSchema(object_name=object_name, fields=fields)
