from __future__ import annotations

from typing import Any

from ..persistence.database import Database
from ..persistence.repositories import AuditRepository, ScanRepository
from .errors import NotFoundError


class DriftService:
    """Compare a scan against its previous completed baseline for the same object."""

    def __init__(
        self,
        *,
        database: Database,
        scans: ScanRepository,
        audit: AuditRepository,
        null_rate_delta: float = 0.10,
        count_swing: float = 2.0,
        mean_shift: float = 0.5,
    ) -> None:
        self.database = database
        self.scans = scans
        self.audit = audit
        self.null_rate_delta = null_rate_delta
        self.count_swing = count_swing
        self.mean_shift = mean_shift

    def compare(self, scan_id: str) -> dict[str, Any]:
        """Return the current scan's drifts relative to the previous completed scan."""
        with self.database.connect() as conn:
            manifest = self.scans.get(conn, scan_id)
            if manifest is None:
                raise NotFoundError("Scan not found")

            prev_id = self.scans.previous_scan_id(conn, manifest["object_name"], manifest["created_at"])
            if prev_id is None:
                return {
                    "scan_id": scan_id,
                    "object_name": manifest["object_name"],
                    "baseline_scan_id": None,
                    "drifts": [],
                    "drift_count": 0,
                }

            baseline = self.scans.get(conn, prev_id)
            drifts = self._detect_drifts(
                current_profile=manifest.get("profile"),
                baseline_profile=None if baseline is None else baseline.get("profile"),
            )
            if drifts:
                self.audit.append(
                    conn,
                    event="drift_detected",
                    actor="system",
                    scan_id=scan_id,
                    object_name=manifest["object_name"],
                    detail={
                        "drift_count": len(drifts),
                        "kinds": sorted({drift["kind"] for drift in drifts}),
                    },
                )

        return {
            "scan_id": scan_id,
            "object_name": manifest["object_name"],
            "baseline_scan_id": prev_id,
            "drifts": drifts,
            "drift_count": len(drifts),
        }

    def _detect_drifts(
        self,
        *,
        current_profile: dict[str, Any] | None,
        baseline_profile: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        if not current_profile or not baseline_profile:
            return []

        drifts: list[dict[str, Any]] = []
        baseline_count = baseline_profile.get("record_count", 0)
        current_count = current_profile.get("record_count", 0)
        volume_ratio = current_count / max(1, baseline_count)
        if volume_ratio >= self.count_swing or volume_ratio <= (1 / self.count_swing):
            drifts.append(
                {
                    "field": "_record_count",
                    "kind": "volume_swing",
                    "before": baseline_count,
                    "after": current_count,
                    "why": f"Record count changed from {baseline_count} to {current_count}.",
                }
            )

        current_fields = current_profile.get("fields") or {}
        baseline_fields = baseline_profile.get("fields") or {}
        for field in current_fields.keys() & baseline_fields.keys():
            current_stats = current_fields[field]
            baseline_stats = baseline_fields[field]

            baseline_null_rate = baseline_stats.get("null_rate")
            current_null_rate = current_stats.get("null_rate")
            if (
                baseline_null_rate is not None
                and current_null_rate is not None
                and abs(current_null_rate - baseline_null_rate) >= self.null_rate_delta
            ):
                drifts.append(
                    {
                        "field": field,
                        "kind": "null_rate_shift",
                        "before": baseline_null_rate,
                        "after": current_null_rate,
                        "why": (
                            f"Null rate changed by {round(abs(current_null_rate - baseline_null_rate), 3)} "
                            f"from {baseline_null_rate} to {current_null_rate}."
                        ),
                    }
                )

            baseline_top = {value for value, _count in baseline_stats.get("top_values", [])}
            current_top = {value for value, _count in current_stats.get("top_values", [])}
            new_top_values = sorted(current_top - baseline_top)
            if new_top_values:
                drifts.append(
                    {
                        "field": field,
                        "kind": "new_top_values",
                        "before": sorted(baseline_top),
                        "after": new_top_values,
                        "why": f"New top values appeared: {', '.join(new_top_values)}.",
                    }
                )

            baseline_numeric = baseline_stats.get("numeric")
            current_numeric = current_stats.get("numeric")
            if baseline_numeric and current_numeric:
                baseline_mean = baseline_numeric.get("mean")
                current_mean = current_numeric.get("mean")
                if baseline_mean is not None and current_mean is not None:
                    mean_delta = abs(current_mean - baseline_mean) / max(1.0, abs(baseline_mean))
                    if mean_delta >= self.mean_shift:
                        drifts.append(
                            {
                                "field": field,
                                "kind": "mean_shift",
                                "before": baseline_mean,
                                "after": current_mean,
                                "why": f"Mean shifted from {baseline_mean} to {current_mean}.",
                            }
                        )

        return drifts
