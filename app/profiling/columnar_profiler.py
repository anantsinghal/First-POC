from __future__ import annotations

from pathlib import Path

from app.domain.models import FieldProfile, Profile

try:
    import duckdb

    _DUCKDB = True
except Exception:
    _DUCKDB = False


class ColumnarProfiler:
    """Compute field profiles from Parquet using DuckDB SQL."""

    def is_available(self) -> bool:
        """Return whether DuckDB profiling is available."""
        return _DUCKDB

    def profile(self, parquet_path: str | Path, fields: list[str]) -> Profile:
        """Profile a Parquet file and return the standard Profile shape."""
        if not _DUCKDB:
            raise RuntimeError("duckdb not installed; install the 'scale' extra")

        relation = _parquet_relation(parquet_path)
        con = duckdb.connect()
        try:
            record_count = int(con.execute(f"SELECT COUNT(*) FROM {relation}").fetchone()[0])
            field_profiles: dict[str, FieldProfile] = {}
            for field in fields:
                identifier = _quote_identifier(field)
                non_empty = int(
                    con.execute(
                        f"""
                        SELECT COUNT(*)
                        FROM {relation}
                        WHERE {identifier} IS NOT NULL AND trim({identifier}) <> ''
                        """
                    ).fetchone()[0]
                )
                distinct_count = int(
                    con.execute(
                        f"""
                        SELECT COUNT(DISTINCT {identifier})
                        FROM {relation}
                        WHERE {identifier} IS NOT NULL AND trim({identifier}) <> ''
                        """
                    ).fetchone()[0]
                )
                top_values = [
                    (str(value), int(count))
                    for value, count in con.execute(
                        f"""
                        WITH ranked AS (
                            SELECT
                                {identifier} AS v,
                                ROW_NUMBER() OVER () AS pos
                            FROM {relation}
                            WHERE trim({identifier}) <> ''
                        )
                        SELECT v, COUNT(*) AS c
                        FROM ranked
                        GROUP BY v
                        ORDER BY c DESC, MIN(pos)
                        LIMIT 5
                        """
                    ).fetchall()
                ]

                cast_expr = f"TRY_CAST(REPLACE({identifier}, ',', '') AS DOUBLE)"
                numeric_count = int(
                    con.execute(f"SELECT COUNT({cast_expr}) FROM {relation}").fetchone()[0]
                )
                numeric: dict[str, float] | None = None
                if numeric_count >= 2:
                    min_value, max_value, avg_value, median_value = con.execute(
                        f"""
                        SELECT
                            MIN({cast_expr}),
                            MAX({cast_expr}),
                            AVG({cast_expr}),
                            MEDIAN({cast_expr})
                        FROM {relation}
                        """
                    ).fetchone()
                    numeric = {
                        "min": float(min_value),
                        "max": float(max_value),
                        "mean": round(float(avg_value), 2),
                        "median": round(float(median_value), 2),
                    }

                field_profiles[field] = FieldProfile(
                    null_rate=round(1 - non_empty / record_count, 3) if record_count else 0,
                    distinct_count=distinct_count,
                    top_values=top_values,
                    numeric=numeric,
                )

            return Profile(record_count=record_count, fields=field_profiles)
        finally:
            con.close()


def _quote_identifier(identifier: str) -> str:
    """Quote a DuckDB identifier safely."""
    return '"' + identifier.replace('"', '""') + '"'


def _parquet_relation(parquet_path: str | Path) -> str:
    """Return the DuckDB relation for reading the target Parquet file."""
    path = str(Path(parquet_path).resolve()).replace("'", "''")
    return f"read_parquet('{path}')"
