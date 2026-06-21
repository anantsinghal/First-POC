from __future__ import annotations

import csv
import hashlib
import io

from ..domain.models import Record
from ..domain.values import clean
from .base import DataSource, IngestionError, LoadedDataset


class CsvSource(DataSource):
    """CSV data source; implements DataSource protocol for unified ingestion."""

    def __init__(self, *, raw_bytes: bytes, object_name: str, source_name: str) -> None:
        """Store raw bytes and metadata; actual parse happens in load()."""
        self.raw_bytes = raw_bytes
        self.object_name = object_name
        self.source_name = source_name

    async def load(self) -> LoadedDataset:
        """Parse CSV exactly as baseline, using clean; raise IngestionError on decode/csv errors."""
        try:
            text = self.raw_bytes.decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(text))
            fields = reader.fieldnames or []
            records: list[Record] = [
                {k: clean(v) for k, v in row.items() if k is not None} for row in reader
            ]
        except (UnicodeDecodeError, csv.Error) as exc:
            raise IngestionError(f"Could not parse CSV: {exc}", status_code=400) from exc
        source_hash = hashlib.sha256(self.raw_bytes).hexdigest()
        return LoadedDataset(
            object_name=self.object_name,
            source_name=self.source_name,
            fields=fields,
            records=records,
            source_hash=source_hash,
        )
