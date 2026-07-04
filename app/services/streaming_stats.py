from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..detection.base import Candidate, DetectionContext, make_candidate
from ..detection.ml.isolation_forest import StreamingIsolationForest
from ..detection.statistical.thresholds import MAD_THRESHOLD, iqr_fences, mad_z
from ..domain.values import clean, parse_float

try:
    import duckdb
except Exception:
    duckdb = None

try:
    import pyarrow.parquet as pq
except Exception:
    pq = None


class StreamingStatsPass:
    """Pass-2 streamed stats over staged Parquet.

    Honesty note: streamed IQR uses DuckDB continuous quantiles, while the in-memory detector uses
    index-based quartiles. Small-n values can differ slightly; at streaming scale the difference is
    negligible, and evidence explicitly marks the streamed method.
    """

    def numeric_aggregates(self, parquet_path: str | Path, fields: list[str]) -> dict[str, dict[str, float]]:
        if duckdb is None:
            raise RuntimeError("duckdb not installed")
        con = duckdb.connect()
        relation = _parquet_relation(parquet_path)
        try:
            aggregates: dict[str, dict[str, float]] = {}
            for field in fields:
                identifier = _quote_identifier(field)
                cast_expr = f"TRY_CAST(REPLACE({identifier}, ',', '') AS DOUBLE)"
                row = con.execute(
                    f"""
                    SELECT
                        COUNT({cast_expr}) AS n,
                        quantile_cont({cast_expr}, 0.25) AS q1,
                        quantile_cont({cast_expr}, 0.5) AS med,
                        quantile_cont({cast_expr}, 0.75) AS q3
                    FROM {relation}
                    """
                ).fetchone()
                n = int(row[0] or 0)
                if n < 8:
                    continue
                q1, med, q3 = row[1], row[2], row[3]
                mad = con.execute(
                    f"""
                    WITH values AS (
                        SELECT {cast_expr} AS x
                        FROM {relation}
                        WHERE {cast_expr} IS NOT NULL
                    )
                    SELECT median(abs(x - ?)) FROM values
                    """,
                    [float(med)],
                ).fetchone()[0]
                aggregates[field] = {
                    "n": n,
                    "q1": float(q1),
                    "med": float(med),
                    "q3": float(q3),
                    "mad": float(mad) if mad is not None else 0.0,
                }
            return aggregates
        finally:
            con.close()

    def flag_batches(
        self,
        parquet_path: str | Path,
        fields: list[str],
        aggregates: dict[str, dict[str, float]],
        *,
        object_name: str,
        policy,
        batch_size: int = 5000,
    ) -> Iterator[list[Candidate]]:
        if pq is None:
            raise RuntimeError("pyarrow not installed")
        parquet_file = pq.ParquetFile(str(parquet_path))
        for batch in parquet_file.iter_batches(batch_size=batch_size):
            records = _batch_to_records(batch)
            ctx = DetectionContext(object_name, records, fields, policy)
            candidates: list[Candidate] = []
            for field in fields:
                stats = aggregates.get(field)
                if stats is None:
                    continue
                fences = iqr_fences(stats["q1"], stats["q3"])
                for record in records:
                    value = parse_float(record.get(field))
                    if value is None:
                        continue
                    if fences is not None:
                        lower, upper = fences
                        if value < lower or value > upper:
                            candidates.append(
                                make_candidate(
                                    ctx,
                                    record,
                                    rule="numeric_outlier",
                                    title=f"Unusual {field} value",
                                    field_name=field,
                                    evidence={
                                        "observed_value": value,
                                        "method": "IQR (streamed)",
                                        "lower_bound": round(lower, 2),
                                        "upper_bound": round(upper, 2),
                                        "why": "The value falls outside the 1.5xIQR range computed from streamed continuous quantiles for this scan.",
                                    },
                                )
                            )
                    if stats["mad"] <= 0:
                        continue
                    mz = mad_z(value, stats["med"], stats["mad"])
                    if abs(mz) > MAD_THRESHOLD:
                        candidates.append(
                            make_candidate(
                                ctx,
                                record,
                                rule="robust_outlier",
                                title=f"Unusual {field} value (robust)",
                                field_name=field,
                                evidence={
                                    "observed_value": value,
                                    "method": "MAD modified z-score (streamed)",
                                    "median": round(stats["med"], 2),
                                    "mad": round(stats["mad"], 2),
                                    "modified_z": round(mz, 2),
                                    "threshold": MAD_THRESHOLD,
                                    "why": "The value is far from the streamed median relative to the median absolute deviation (robust to skew).",
                                },
                            )
                        )
            yield candidates

    def score_ml_batches(
        self,
        parquet_path: str | Path,
        fields: list[str],
        forest: StreamingIsolationForest,
        *,
        object_name: str,
        policy,
        batch_size: int = 5000,
    ) -> Iterator[list[Candidate]]:
        if pq is None:
            raise RuntimeError("pyarrow not installed")
        parquet_file = pq.ParquetFile(str(parquet_path))
        for batch in parquet_file.iter_batches(batch_size=batch_size):
            records = _batch_to_records(batch)
            ctx = DetectionContext(object_name, records, fields, policy)
            scored = forest.score_batch(records)
            if not scored:
                yield []
                continue
            deltas = [forest._threshold - score for _, score, _ in scored]
            range_min, range_max = min(deltas), max(deltas)
            range_span = range_max - range_min if range_max > range_min else 1.0
            candidates: list[Candidate] = []
            for idx, score, top in scored:
                strength = ((forest._threshold - score) - range_min) / range_span if range_span > 0 else 0.0
                confidence = round(min(0.95, 0.6 + 0.35 * max(0.0, strength)), 2)
                top_str = ", ".join(f"{item['feature']} (z={item['z']})" for item in top)
                why = (
                    f"This record is unusual vs. a sample-trained model over {forest.sample_size} records; "
                    f"most unusual: {top_str}. Human review required."
                )
                candidates.append(
                    make_candidate(
                        ctx,
                        records[idx],
                        rule="ml_anomaly",
                        title="Statistically unusual record (ML)",
                        field_name=None,
                        evidence={
                            "method": "Isolation Forest (sampled, streamed)",
                            "anomaly_score": round(float(score), 4),
                            "sample_size": forest.sample_size,
                            "top_contributors": top,
                            "why": why,
                        },
                        confidence=confidence,
                    )
                )
            yield candidates


def _batch_to_records(batch) -> list[dict[str, str]]:
    rows = batch.to_pylist()
    return [
        {key: clean("" if value is None else str(value)) for key, value in row.items()}
        for row in rows
    ]


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _parquet_relation(parquet_path: str | Path) -> str:
    path = str(Path(parquet_path).resolve()).replace("'", "''")
    return f"read_parquet('{path}')"
