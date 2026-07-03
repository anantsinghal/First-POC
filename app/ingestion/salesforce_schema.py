from __future__ import annotations

from typing import Any

from ..domain.schema import FieldDescriptor, FieldType, ObjectSchema


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
        elif ftype == "email":
            data_type = FieldType.EMAIL
        elif ftype == "phone":
            data_type = FieldType.PHONE
        elif ftype == "url":
            data_type = FieldType.URL
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
            length=field.get("length"),
        )
        if fd.name:
            fields[fd.name] = fd

    return ObjectSchema(object_name=object_name, fields=fields)
