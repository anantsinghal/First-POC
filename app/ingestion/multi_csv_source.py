from __future__ import annotations

import hashlib

from .base import DataSource, IngestionError, LoadedDataset
from .csv_source import parse_csv_bytes


class MultiCsvSource(DataSource):
    """CSV source for one primary object plus related object CSV files."""

    def __init__(
        self, *, primary_object: str, files: list[tuple[str, bytes]], source_name: str
    ) -> None:
        """Store primary object, uploaded object CSV bytes, and source label."""
        self.primary_object = primary_object
        self.files = files
        self.source_name = source_name

    async def load(self) -> LoadedDataset:
        """Parse all CSV files and return a dataset with related records attached."""
        parsed = {
            object_name: parse_csv_bytes(raw_bytes) for object_name, raw_bytes in self.files
        }
        if self.primary_object not in parsed:
            raise IngestionError(
                f"Primary object '{self.primary_object}' was not among the uploaded files.",
                status_code=400,
            )

        primary_fields, primary_records = parsed[self.primary_object]
        related_records = {
            obj: recs for obj, (_flds, recs) in parsed.items() if obj != self.primary_object
        }
        source_hash = hashlib.sha256(
            b"".join(raw for _, raw in sorted(self.files))
        ).hexdigest()
        return LoadedDataset(
            object_name=self.primary_object,
            source_name=self.source_name,
            fields=primary_fields,
            records=primary_records,
            source_hash=source_hash,
            related_records=related_records,
        )
