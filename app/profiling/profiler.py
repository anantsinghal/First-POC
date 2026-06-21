from __future__ import annotations

import statistics
from collections import Counter
from typing import Any

from ..domain.models import FieldProfile, Profile, Record
from ..domain.values import clean, numeric_values


class Profiler:
    """Profiler that extracts per-field statistics mirroring the baseline profile_records exactly."""

    def profile(self, records: list[Record], fields: list[str]) -> Profile:
        """Return Profile with FieldProfile per field; numeric omitted when <2 values; matches baseline to_dict."""
        field_profiles: dict[str, FieldProfile] = {}
        for field in fields:
            values = [clean(row.get(field)) for row in records]
            non_empty = [value for value in values if value]
            details: dict[str, Any] = {
                "null_rate": round(1 - len(non_empty) / len(records), 3) if records else 0,
                "distinct_count": len(set(non_empty)),
                "top_values": Counter(non_empty).most_common(5),
            }
            numbers = numeric_values(records, field)
            if len(numbers) >= 2:
                details["numeric"] = {
                    "min": min(numbers),
                    "max": max(numbers),
                    "mean": round(statistics.mean(numbers), 2),
                    "median": round(statistics.median(numbers), 2),
                }
            field_profiles[field] = FieldProfile(
                null_rate=details["null_rate"],
                distinct_count=details["distinct_count"],
                top_values=details["top_values"],
                numeric=details.get("numeric"),
            )
        return Profile(record_count=len(records), fields=field_profiles)
