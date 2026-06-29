from __future__ import annotations

from uuid import uuid4

from ..coverage.grader import CoverageGrader
from ..detection.base import DetectionContext
from ..detection.registry import DetectorRegistry
from ..domain.enums import CoverageGrade
from ..domain.values import clean
from ..ingestion.base import DataSource
from ..persistence.database import Database, now
from ..persistence.repositories import AuditRepository, CoverageRepository, FindingRepository, ScanRepository
from ..planning.planner import ScanPlanner
from ..policy.field_policy import FieldPolicy
from ..profiling.profiler import Profiler

from .errors import NotFoundError


class ScanService:
    """Orchestrates a full scan: load via DataSource -> profile -> grade coverage -> detect -> persist atomically."""

    def __init__(
        self,
        *,
        database: Database,
        profiler: Profiler,
        grader: CoverageGrader,
        registry: DetectorRegistry,
        planner: ScanPlanner,
        scans: ScanRepository,
        coverage_repo: CoverageRepository,
        findings: FindingRepository,
        policy: FieldPolicy,
        audit: AuditRepository | None = None,
    ) -> None:
        """Store injected dependencies (no defaults; wired in composition root)."""
        self.database = database
        self.profiler = profiler
        self.grader = grader
        self.registry = registry
        self.planner = planner
        self.scans = scans
        self.coverage_repo = coverage_repo
        self.findings = findings
        self.policy = policy
        self.audit = audit

    async def run_scan(self, source: DataSource) -> dict:
        """Execute the scan pipeline. Load first (no DB side effects on failure). Use single UoW for writes."""
        self.database.setup()
        dataset = await source.load()
        scan_id = str(uuid4())
        created = now()
        with self.database.connect() as conn:
            self.scans.insert_running(
                conn,
                scan_id=scan_id,
                source_name=dataset.source_name,
                object_name=dataset.object_name,
                created_at=created,
                row_count=len(dataset.records),
                source_hash=dataset.source_hash,
                fields=dataset.fields,
            )
            coverage = self.grader.grade(
                dataset.object_name, dataset.records, dataset.fields,
                sampled=getattr(dataset, "truncated", False),
            )
            self.coverage_repo.insert(conn, scan_id, coverage)
            profile = self._profile(dataset)
            if coverage.grade == CoverageGrade.BLOCKED:
                candidates = []
                plan = None
            else:
                related_ids = dataset.related_ids or {
                    obj: frozenset(clean(r.get("Id")) for r in recs if clean(r.get("Id")))
                    for obj, recs in (dataset.related_records or {}).items()
                }
                ctx = DetectionContext(
                    dataset.object_name, dataset.records, dataset.fields, self.policy, profile,
                    schema=dataset.schema,
                    related_ids=related_ids,
                    related_records=dataset.related_records or {},
                )
                # The row count is the current estimate; production will supply a pre-extraction count.
                plan = self.planner.plan(dataset.object_name, len(dataset.records))
                candidates = self.registry.detect(ctx, include=plan.included_ids)
            self.findings.upsert_many(
                conn,
                scan_id=scan_id,
                candidates=candidates,
                source_hash=dataset.source_hash,
                scan_created_at=created,
            )
            self.scans.complete(
                conn,
                scan_id=scan_id,
                completed_at=now(),
                profile=profile.to_dict(),
                notes=(
                    f"{len(candidates)} candidate occurrences generated."
                    if plan is None
                    else f"{len(candidates)} candidate occurrences generated. {plan.notes}"
                ),
            )
            # Snapshot quality counts at completion so trend is stable even after findings'
            # scan_id migrates to later scans on re-detection.
            self.scans.save_quality(
                conn,
                scan_id=scan_id,
                observed_records=coverage.observed_records,
                flagged_records=len({c.record_id for c in candidates if c.record_id}),
            )
            if dataset.schema is not None:
                self.scans.save_schema(
                    conn, scan_id=scan_id, object_name=dataset.object_name, schema=dataset.schema.to_dict()
                )
            if self.audit is not None:
                self.audit.append(
                    conn,
                    event="scan_completed",
                    actor="system",
                    scan_id=scan_id,
                    object_name=dataset.object_name,
                    detail={
                        "finding_count": len(candidates),
                        "coverage_grade": coverage.grade.value,
                        "row_count": len(dataset.records),
                        "deferred_detectors": [] if plan is None else [detector_id for detector_id, _ in plan.skipped],
                        "planned_detectors": 0 if plan is None else len(plan.included_ids),
                    },
                )
        return self.get_scan_detail(scan_id)

    def _profile(self, dataset):
        """Profile the dataset, out-of-core (Parquet+DuckDB) for large objects per the planner.

        For large objects the records are staged to an EPHEMERAL Parquet file, profiled via DuckDB
        (bounded memory), then the staged file is purged. Falls back to the in-memory profiler when the
        object is small or the optional scale deps (duckdb/pyarrow) are unavailable. The Profile shape
        is identical either way (verified parity), so downstream behaviour is unchanged.
        """
        records, fields = dataset.records, dataset.fields
        if self._columnar_available() and self.planner.use_columnar_profile(len(records)):
            import os
            import tempfile

            from ..profiling.columnar_profiler import ColumnarProfiler
            from ..staging.parquet_store import write_records

            staging_dir = tempfile.mkdtemp(prefix="scan_stage_")
            path = os.path.join(staging_dir, "records.parquet")
            try:
                write_records(path, records, fields)
                return ColumnarProfiler().profile(path, fields)
            finally:
                # Ephemeral staging: purge the raw columnar data immediately after profiling.
                try:
                    os.remove(path)
                except OSError:
                    pass
                try:
                    os.rmdir(staging_dir)
                except OSError:
                    pass
        return self.profiler.profile(records, fields)

    async def profile_source(self, source: DataSource) -> dict:
        """Bounded-memory PROFILE-ONLY scan for large objects: stream -> Parquet -> columnar profile.

        Streams the source's batches straight into an ephemeral Parquet file (records never fully
        materialised), profiles out-of-core via DuckDB, and persists manifest + coverage + profile +
        quality (NO findings — detection is a separate pass). Falls back to load() + in-memory profile
        when the source can't stream or the scale deps are absent.
        """
        from ..domain.models import stable_hash

        self.database.setup()
        object_name = getattr(source, "object_name", "Unknown")
        source_name = getattr(source, "source_name", "stream-profile")
        scan_id = str(uuid4())
        created = now()

        if hasattr(source, "iter_batches") and self._columnar_available():
            import os
            import tempfile

            from ..profiling.columnar_profiler import ColumnarProfiler
            from ..staging.parquet_store import write_record_batches

            staging_dir = tempfile.mkdtemp(prefix="scan_profile_")
            path = os.path.join(staging_dir, "records.parquet")
            try:
                generator = source.iter_batches()
                first_batch = next(generator, None)
                columns = list(first_batch[0].keys()) if first_batch else list(
                    getattr(source, "fieldnames", []) or []
                )

                def all_batches():
                    if first_batch:
                        yield first_batch
                    yield from generator

                total = write_record_batches(path, all_batches(), columns) if columns else 0
                profile = (
                    ColumnarProfiler().profile(path, columns)
                    if total
                    else self.profiler.profile([], columns)
                )
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass
                try:
                    os.rmdir(staging_dir)
                except OSError:
                    pass
            source_hash = stable_hash({"object": object_name, "fields": columns, "count": total, "mode": "stream-profile"})
            fields = columns
            mode = "stream-profile"
        else:
            dataset = await source.load()
            fields = dataset.fields
            total = len(dataset.records)
            profile = self._profile(dataset)
            source_hash = dataset.source_hash
            object_name = dataset.object_name
            source_name = dataset.source_name
            mode = "profile"

        coverage = self.grader.grade(object_name, [], fields, record_count=total)
        with self.database.connect() as conn:
            self.scans.insert_running(
                conn, scan_id=scan_id, source_name=source_name, object_name=object_name,
                created_at=created, row_count=total, source_hash=source_hash, fields=fields,
            )
            self.coverage_repo.insert(conn, scan_id, coverage)
            self.scans.complete(
                conn, scan_id=scan_id, completed_at=now(),
                profile=profile.to_dict(), notes=f"profile-only scan ({mode}); detection not run.",
            )
            self.scans.save_quality(conn, scan_id=scan_id, observed_records=total, flagged_records=0)
            if self.audit is not None:
                self.audit.append(
                    conn, event="scan_profiled", actor="system", scan_id=scan_id,
                    object_name=object_name,
                    detail={"row_count": total, "coverage_grade": coverage.grade.value, "mode": mode},
                )
        return self.get_scan_detail(scan_id)

    @staticmethod
    def _columnar_available() -> bool:
        """True when both optional scale deps (pyarrow staging + duckdb profiler) are importable."""
        try:
            from ..profiling.columnar_profiler import ColumnarProfiler
            from ..staging.parquet_store import is_available as pyarrow_available

            return pyarrow_available() and ColumnarProfiler().is_available()
        except Exception:
            return False

    def get_scan_detail(self, scan_id: str) -> dict:
        """Return manifest + coverage + finding_count (raises NotFoundError if missing). Matches old scan_detail shape."""
        with self.database.connect() as conn:
            manifest = self.scans.get(conn, scan_id)
            if manifest is None:
                raise NotFoundError("Scan not found")
            cov = self.coverage_repo.get(conn, scan_id)
            count = self.findings.count_for_scan(conn, scan_id)
            schema = self.scans.get_schema(conn, scan_id)
        manifest["coverage"] = cov
        manifest["finding_count"] = count
        manifest["schema"] = schema
        return manifest

    def list_scans(self) -> list[dict]:
        """List recent scans."""
        with self.database.connect() as conn:
            return self.scans.list(conn)
