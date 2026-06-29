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


def write_records(path: str | Path, records: list[Record], columns: list[str]) -> None:
    """Write a list of records to a Parquet file with all columns as strings."""
    if not _PYARROW:
        raise RuntimeError("pyarrow not installed; install the 'scale' extra")
    pq.write_table(_table_from_records(records, columns), Path(path))


def write_record_batches(
    path: str | Path, batches: Iterable[list[Record]], columns: list[str]
) -> int:
    """Write record batches to Parquet incrementally and return the row count."""
    if not _PYARROW:
        raise RuntimeError("pyarrow not installed; install the 'scale' extra")

    schema = _schema(columns)
    writer: pq.ParquetWriter | None = None
    total_rows = 0
    target = Path(path)

    try:
        for batch in batches:
            table = _table_from_records(batch, columns)
            if writer is None:
                writer = pq.ParquetWriter(target, schema)
            writer.write_table(table)
            total_rows += len(batch)
    finally:
        if writer is not None:
            writer.close()

    if writer is None:
        pq.write_table(_table_from_records([], columns), target)
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
