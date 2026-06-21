from __future__ import annotations

from datetime import date, datetime
from typing import Any


def clean(value: str | None) -> str:
    """Return stripped string or empty if None."""
    return (value or "").strip()


def display_name(record: dict[str, str]) -> str:
    """Return Name or Id (cleaned) for display."""
    return clean(record.get("Name")) or clean(record.get("Id"))


def parse_float(raw: str | None) -> float | None:
    """Parse cleaned value as float (handling commas), return None on failure."""
    try:
        s = clean(raw)
        if not s:
            return None
        return float(s.replace(",", ""))
    except (ValueError, TypeError):
        return None


def numeric_values(records: list[dict[str, str]], field: str) -> list[float]:
    """Extract list of parsed floats for the field across records, skipping unparsable/empty."""
    values: list[float] = []
    for record in records:
        v = parse_float(record.get(field))
        if v is not None:
            values.append(v)
    return values


def parse_date(raw: str | None) -> date | None:
    """Parse cleaned value as YYYY-MM-DD date; return None on empty or parse failure."""
    try:
        s = clean(raw)
        if not s:
            return None
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None
