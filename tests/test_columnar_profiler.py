from __future__ import annotations

import pytest

pytest.importorskip("duckdb")
pytest.importorskip("pyarrow")

from app.profiling.columnar_profiler import ColumnarProfiler
from app.profiling.profiler import Profiler
from app.staging.parquet_store import write_record_batches, write_records
from tests.harness import demo_records


def test_columnar_profiler_matches_in_memory_demo(tmp_path) -> None:
    """DuckDB profile output should match the in-memory profiler on the demo fixture."""
    records, fields = demo_records()
    parquet_path = tmp_path / "demo.parquet"

    write_records(parquet_path, records, fields)

    columnar_profile = ColumnarProfiler().profile(parquet_path, fields)
    in_memory_profile = Profiler().profile(records, fields)

    assert columnar_profile.record_count == in_memory_profile.record_count == 11
    for field in fields:
        columnar_field = columnar_profile.fields[field]
        in_memory_field = in_memory_profile.fields[field]
        assert columnar_field.distinct_count == in_memory_field.distinct_count
        assert abs(columnar_field.null_rate - in_memory_field.null_rate) < 1e-9
        assert set(columnar_field.top_values) == set(in_memory_field.top_values)
        assert (columnar_field.numeric is None) == (in_memory_field.numeric is None)
        if columnar_field.numeric is not None and in_memory_field.numeric is not None:
            for key in ("min", "max", "mean", "median"):
                assert abs(columnar_field.numeric[key] - in_memory_field.numeric[key]) < 1e-6


def test_columnar_profiler_scales_from_streamed_parquet_batches(tmp_path) -> None:
    """Large profiles should run from streamed Parquet batches without one giant Python list."""
    parquet_path = tmp_path / "scale.parquet"
    fields = ["Id", "Segment", "Amount"]

    def batches():
        for batch_index in range(20):
            start = batch_index * 10_000
            yield [
                {
                    "Id": f"00Q{row_index:06d}",
                    "Segment": "Enterprise" if row_index % 2 == 0 else "SMB",
                    "Amount": str(row_index),
                }
                for row_index in range(start, start + 10_000)
            ]

    total_rows = write_record_batches(parquet_path, batches(), fields)
    profile = ColumnarProfiler().profile(parquet_path, fields)

    assert total_rows == 200_000
    assert profile.record_count == 200_000
    assert profile.fields["Segment"].distinct_count == 2
    assert profile.fields["Amount"].numeric is not None
    assert profile.fields["Amount"].numeric["min"] == 0.0
    assert profile.fields["Amount"].numeric["max"] == 199999.0
