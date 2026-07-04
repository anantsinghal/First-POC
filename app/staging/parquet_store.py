from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from app.domain.models import Record

try:
    import pyarrow as pa
    import pyarrow.parquet as pq

    _PYARROW = True
except Exception:
    _PYARROW = False


def is_available() -> bool:
    """Return whether pyarrow-backed staging is available."""
    return _PYARROW


class BatchParquetWriter:
    """Incrementally write cleaned record batches to a Parquet file."""

    def __init__(self, path: str | Path, columns: list[str]) -> None:
        if not _PYARROW:
            raise RuntimeError("pyarrow not installed; install the 'scale' extra")
        self.path = Path(path)
        self.columns = columns
        self._writer = pq.ParquetWriter(self.path, _schema(columns))

    def __enter__(self) -> BatchParquetWriter:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def write(self, records: list[Record]) -> None:
        """Write one batch using the all-string staging schema."""
        self._writer.write_table(_table_from_records(records, self.columns))

    def close(self) -> None:
        """Close the underlying writer."""
        self._writer.close()


def write_records(path: str | Path, records: list[Record], columns: list[str]) -> None:
    """Write a list of records to a Parquet file with all columns as strings."""
    if not _PYARROW:
        raise RuntimeError("pyarrow not installed; install the 'scale' extra")
    pq.write_table(_table_from_records(records, columns), Path(path))


def write_record_batches(
    path: str | Path, batches: Iterable[list[Record]], columns: list[str]
) -> int:
    """Write record batches to Parquet incrementally and return the row count."""
    total_rows = 0
    with BatchParquetWriter(path, columns) as writer:
        for batch in batches:
            writer.write(batch)
            total_rows += len(batch)
    return total_rows


def _schema(columns: list[str]) -> pa.Schema:
    """Build the all-string schema required by the staging format."""
    return pa.schema([(column, pa.string()) for column in columns])


def _table_from_records(records: list[Record], columns: list[str]) -> pa.Table:
    """Convert cleaned string records into a consistent pyarrow table."""
    return pa.table(
        {
            column: [str(record.get(column, "") or "") for record in records]
            for column in columns
        },
        schema=_schema(columns),
    )
