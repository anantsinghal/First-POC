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
    EMAIL = "email"
    PHONE = "phone"
    URL = "url"


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
    length: int | None = None


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

    def format_fields(self) -> list[FieldDescriptor]:
        """Fields whose data_type is EMAIL, PHONE, or URL (for format validation)."""
        return [fd for fd in self.fields.values()
                if fd.data_type in {FieldType.EMAIL, FieldType.PHONE, FieldType.URL}]

    def to_dict(self) -> dict[str, Any]:
        """Return dict shape that round-trips with schema_from_dict (same fields after reconstruction)."""
        field_list: list[dict[str, Any]] = []
        for name in sorted(self.fields.keys()):
            fd = self.fields[name]
            field_list.append({
                "name": fd.name,
                "data_type": fd.data_type.value,
                "required": fd.required,
                "picklist_values": sorted(fd.picklist_values) if fd.picklist_values is not None else None,
                "reference_to": list(fd.reference_to),
                "is_name_field": fd.is_name_field,
                "is_unique": fd.is_unique,
                "length": fd.length,
            })
        return {"object_name": self.object_name, "fields": field_list}


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
            length=f.get("length"),
        )
        if fd.name:
            fields[fd.name] = fd
    return ObjectSchema(object_name=object_name, fields=fields)
