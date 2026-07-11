from __future__ import annotations

import asyncio
import heapq
import random
from pathlib import Path
from uuid import uuid4

from ..coverage.grader import CoverageGrader
from ..detection.base import DetectionContext
from ..detection.registry import DetectorRegistry
from ..domain.enums import CoverageGrade
from ..domain.models import Record, stable_hash
from ..domain.values import clean
from ..ingestion.base import DataSource
from ..persistence.database import Database, now
from ..persistence.repositories import AuditRepository, CoverageRepository, FindingRepository, ScanRepository
from ..persistence.source_key import source_key_from_name
from ..planning.planner import DETECTOR_TIERS, ScanPlanner
from ..policy.field_policy import FieldPolicy
from ..profiling.profiler import Profiler
from .streaming_stats import StreamingStatsPass
from ..detection.ml.isolation_forest import StreamingIsolationForest

from .errors import NotFoundError


STREAMED_IQR_MULTIPLIER = 3.0
"""Far-outlier fence for the streamed numeric_outlier/robust_outlier pass. Wider than the in-memory
detector's fixed 1.5xIQR (app/detection/statistical/thresholds.IQR_MULTIPLIER) on purpose: at scale
(millions of rows) a narrow fence flags a routine 0.7% of gaussian data, flooding the review queue,
so the streamed pass only surfaces genuinely far outliers. The difference is intentional and is
documented in each streamed finding's evidence 'why'."""

MAX_STREAMED_ML_FINDINGS = 500
"""Cap on ml_anomaly findings persisted from a single streamed scan. The sampled Isolation Forest
flags ~2% of all rows by construction; on a multi-million-row object that's tens of thousands of
low-value candidates. Only the top-K by |anomaly_score| are kept (a small heap, so memory stays
bounded regardless of how many rows were scored); the scan is honest about the cap in its notes and
audit detail (ml_flagged_total vs ml_kept)."""


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

    async def run_scan(
        self, source: DataSource, *, include_override: frozenset[str] | None = None
    ) -> dict:
        """Execute the scan pipeline. Load first (no DB side effects on failure). Use single UoW for writes.

        include_override: when set, bypass the planner and run only that detector id set. Used by
        incremental delta scans so statistical/ML baselines are never computed on a changes-only
        population (which would be statistically invalid).
        """
        self.database.setup()
        dataset = await source.load()
        scan_id = str(uuid4())
        created = now()
        src_key = source_key_from_name(dataset.source_name)
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
                source_key=src_key,
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
                if include_override is not None:
                    # Delta path: skip planner; run only contract/rule (and orphan) detectors.
                    plan = None
                    candidates = self.registry.detect(ctx, include=include_override)
                else:
                    # The row count is the current estimate; production will supply a pre-extraction count.
                    plan = self.planner.plan(dataset.object_name, len(dataset.records))
                    candidates = self.registry.detect(ctx, include=plan.included_ids)
            if plan is None:
                notes = f"{len(candidates)} candidate occurrences generated."
            else:
                notes = f"{len(candidates)} candidate occurrences generated. {plan.notes}"
            if include_override is not None:
                notes += (
                    " (delta scan: contract/rule checks only; statistical "
                    "and ML detectors skipped — population is changes-only)"
                )
            self.findings.upsert_many(
                conn,
                scan_id=scan_id,
                candidates=candidates,
                source_hash=dataset.source_hash,
                scan_created_at=created,
                source_key=src_key,
            )
            self.scans.complete(
                conn,
                scan_id=scan_id,
                completed_at=now(),
                profile=profile.to_dict(),
                notes=notes,
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
                if include_override is not None:
                    deferred = sorted(d for d in DETECTOR_TIERS if d not in include_override)
                    planned = len(include_override)
                else:
                    deferred = [] if plan is None else [detector_id for detector_id, _ in plan.skipped]
                    planned = 0 if plan is None else len(plan.included_ids)
                detail = {
                    "finding_count": len(candidates),
                    "coverage_grade": coverage.grade.value,
                    "row_count": len(dataset.records),
                    "deferred_detectors": deferred,
                    "planned_detectors": planned,
                }
                if include_override is not None:
                    detail["delta_scan"] = True
                self.audit.append(
                    conn,
                    event="scan_completed",
                    actor="system",
                    scan_id=scan_id,
                    object_name=dataset.object_name,
                    detail=detail,
                )
        return self.get_scan_detail(scan_id)

    async def stream_scan(
        self, source: DataSource, *, skip_statistical_pass: bool = False
    ) -> dict:
        """Bounded-memory DETECTING scan using iter_batches or aiter_batches when available.

        Runs only tier-0 detectors per batch (plus orphan_reference when related_ids present).
        Uses a single DB connection for the entire write path. The manifest is inserted immediately
        with row_count=0 and row_count is finalized only at completion. Findings from each completed
        batch are checkpointed with an explicit commit, so a crash mid-scan leaves the manifest in
        'running' status and preserves findings from earlier completed batches. Tees record batches
        to ephemeral Parquet (if scale deps) for columnar profile; otherwise empty profile + note.
        Falls back to run_scan when no iter/aiter_batches. Share per-batch via inner fn. All existing
        tests unaffected.

        skip_statistical_pass: when True (incremental delta), do not run pass-2 streamed
        stats/ML — baselines computed on a changes-only population would be statistically invalid.
        """
        self.database.setup()
        scan_id = str(uuid4())
        created = now()

        # Fallback for sources without streaming support
        if not hasattr(source, "iter_batches") and not hasattr(source, "aiter_batches"):
            return await self.run_scan(source)

        # Derive names (sqlite uses table_name; csv uses object_name etc)
        object_name = getattr(source, "object_name", None) or getattr(source, "table_name", "Unknown")
        source_name = getattr(source, "source_name", None)
        if source_name is None:
            if hasattr(source, "db_path") and object_name != "Unknown":
                dbn = Path(getattr(source, "db_path")).name
                source_name = f"sqlite:{dbn}/{object_name}"
            else:
                source_name = f"stream:{object_name}"
        src_key = source_key_from_name(source_name)

        # Prepare to iterate (re-entrant safe; use same iterator object to support one-shot iters)
        # Support async aiter_batches (for Bulk) by peeking first via __anext__; elif sync iter_batches
        sync_iter = None
        async_aiter = None
        first_batch = None
        if hasattr(source, "aiter_batches"):
            async_aiter = source.aiter_batches()
            try:
                first_batch = await async_aiter.__anext__()
            except StopAsyncIteration:
                first_batch = None
        elif hasattr(source, "iter_batches"):
            sync_iter = source.iter_batches()
            first_batch = next(sync_iter, None)

        # Bulk/org sources (aiter_batches) hold real awaits and are never wrapped by run_offloaded
        # (see app/api/routes_scans.py), so their per-batch CPU work is offloaded here instead to
        # keep the request-handling event loop live.
        is_async_source = async_aiter is not None

        fields: list[str] = list(
            getattr(source, "fieldnames", None)
            or (list(first_batch[0].keys()) if first_batch else [])
        )
        schema = getattr(source, "schema", None)
        related_ids = getattr(source, "related_ids", None) or {}
        source_hash = stable_hash({"object": object_name, "fields": fields, "mode": "streamed"})
        columnar_available = self._columnar_available()

        # Tier-0 include set (from planner constant) + orphan_reference opportunistically
        tier0 = frozenset(d for d, t in DETECTOR_TIERS.items() if t == 0)
        if related_ids:
            tier0 = tier0 | {"orphan_reference"}

        total_records = 0
        total_findings = 0
        batches = 0
        flagged: set[str] = set()
        reservoir_rng = random.Random(42)
        reservoir_size = 5_000
        sample_records: list[Record] = []
        with self.database.connect() as conn:
            self.scans.insert_running(
                conn,
                scan_id=scan_id,
                source_name=source_name,
                object_name=object_name,
                created_at=created,
                row_count=0,
                source_hash=source_hash,
                fields=fields,
                source_key=src_key,
            )
            conn.commit()

            def _process_batch(b: list[Record]) -> None:
                """Shared per-batch body (detection + checkpointed persistence)."""
                nonlocal total_records, total_findings, batches
                batches += 1
                for record in b:
                    seen = total_records + 1
                    if len(sample_records) < reservoir_size:
                        sample_records.append(dict(record))
                    else:
                        replace_at = reservoir_rng.randrange(seen)
                        if replace_at < reservoir_size:
                            sample_records[replace_at] = dict(record)
                    total_records = seen
                ctx = DetectionContext(
                    object_name, b, fields, self.policy, None,
                    schema=schema, related_ids=related_ids or {},
                )
                cands = self.registry.detect(ctx, include=tier0)
                self.findings.upsert_many(
                    conn,
                    scan_id=scan_id,
                    candidates=cands,
                    source_hash=source_hash,
                    scan_created_at=created,
                    source_key=src_key,
                )
                conn.commit()
                total_findings += len(cands)
                for c in cands:
                    if c.record_id:
                        flagged.add(c.record_id)

            def _persist_batch(candidate_batch: list) -> None:
                """Shared per-iteration body for the pass-2 streamed-stats/ML loops (checkpointed persistence)."""
                nonlocal total_findings
                self.findings.upsert_many(
                    conn,
                    scan_id=scan_id,
                    candidates=candidate_batch,
                    source_hash=source_hash,
                    scan_created_at=created,
                    source_key=src_key,
                )
                conn.commit()
                total_findings += len(candidate_batch)
                for candidate in candidate_batch:
                    if candidate.record_id:
                        flagged.add(candidate.record_id)

            # Columnar profile via tee if available; detection side-effects happen during consumption
            if columnar_available:
                import os
                import tempfile

                from ..profiling.columnar_profiler import ColumnarProfiler
                from ..staging.parquet_store import BatchParquetWriter

                staging_dir = tempfile.mkdtemp(prefix="scan_stream_")
                path = os.path.join(staging_dir, "records.parquet")
                writer = None
                used_sampled_ml = False
                ran_streamed_stats = False
                ml_fit_skipped = True
                ml_flagged_total = 0
                ml_kept = 0
                try:
                    cols = fields or list(getattr(source, "fieldnames", []))
                    if cols:
                        writer = BatchParquetWriter(path, cols)
                        if is_async_source:
                            if first_batch is not None:
                                await asyncio.to_thread(_process_batch, first_batch)
                                writer.write(first_batch)
                            if async_aiter:
                                async for b in async_aiter:
                                    await asyncio.to_thread(_process_batch, b)
                                    writer.write(b)
                        else:
                            if first_batch is not None:
                                _process_batch(first_batch)
                                writer.write(first_batch)
                            for b in (sync_iter or []):
                                _process_batch(b)
                                writer.write(b)
                        writer.close()
                        writer = None
                        profile = ColumnarProfiler().profile(path, cols)
                        # Incremental delta: pass-2 stats/ML would baseline against the changes-only
                        # population — statistically invalid. Skip them and mark the scan honest.
                        if not skip_statistical_pass:
                            stats_pass = StreamingStatsPass()
                            aggs = stats_pass.numeric_aggregates(path, cols)
                            if aggs:
                                ran_streamed_stats = True
                                for candidate_batch in stats_pass.flag_batches(
                                    path,
                                    cols,
                                    aggs,
                                    object_name=object_name,
                                    policy=self.policy,
                                    iqr_multiplier=STREAMED_IQR_MULTIPLIER,
                                ):
                                    if is_async_source:
                                        await asyncio.to_thread(_persist_batch, candidate_batch)
                                    else:
                                        _persist_batch(candidate_batch)
                            forest = StreamingIsolationForest()
                            ml_fit_skipped = not forest.fit(sample_records, cols)
                            if not ml_fit_skipped:
                                used_sampled_ml = True
                                # Bounded top-K selection by |anomaly_score|: push every scored candidate
                                # into a size-capped min-heap (smallest magnitude evicted first), so memory
                                # never exceeds MAX_STREAMED_ML_FINDINGS regardless of how many rows are
                                # scored. Persisted once, after the generator is fully drained.
                                ml_heap: list[tuple[float, int, object]] = []
                                for candidate_batch in stats_pass.score_ml_batches(
                                    path,
                                    cols,
                                    forest,
                                    object_name=object_name,
                                    policy=self.policy,
                                ):
                                    for candidate in candidate_batch:
                                        ml_flagged_total += 1
                                        magnitude = abs(candidate.evidence.get("anomaly_score", 0.0))
                                        heapq.heappush(ml_heap, (magnitude, ml_flagged_total, candidate))
                                        if len(ml_heap) > MAX_STREAMED_ML_FINDINGS:
                                            heapq.heappop(ml_heap)
                                ml_kept = len(ml_heap)
                                top_ml_candidates = [c for _, _, c in ml_heap]
                                if is_async_source:
                                    await asyncio.to_thread(_persist_batch, top_ml_candidates)
                                else:
                                    _persist_batch(top_ml_candidates)
                    else:
                        profile = self.profiler.profile([], fields)
                        ml_fit_skipped = True
                finally:
                    if writer is not None:
                        writer.close()
                    try:
                        os.remove(path)
                    except OSError:
                        pass
                    try:
                        os.rmdir(staging_dir)
                    except OSError:
                        pass
                profile_note = ""
            else:
                used_sampled_ml = False
                ml_fit_skipped = True
                ml_flagged_total = 0
                ml_kept = 0
                if is_async_source:
                    if first_batch is not None:
                        await asyncio.to_thread(_process_batch, first_batch)
                    if async_aiter:
                        async for b in async_aiter:
                            await asyncio.to_thread(_process_batch, b)
                else:
                    if first_batch is not None:
                        _process_batch(first_batch)
                    for b in (sync_iter or []):
                        _process_batch(b)
                profile = self.profiler.profile([], fields)
                profile_note = "profile skipped (scale deps unavailable)"

            coverage = self.grader.grade(
                object_name, [], fields, record_count=total_records,
                sampled=getattr(source, "truncated", False),
            )
            self.coverage_repo.insert(conn, scan_id, coverage)
            self.scans.set_row_count(conn, scan_id, total_records)
            if skip_statistical_pass:
                notes = (
                    f"streamed scan: {total_findings} findings from tier-0 detectors over {batches} batches"
                    " (delta scan: contract/rule checks only; statistical "
                    "and ML detectors skipped — population is changes-only)"
                )
            elif columnar_available:
                streamed_suffix = " + sampled ML" if used_sampled_ml else ""
                notes = (
                    f"streamed scan: {total_findings} findings (tier-0 + streamed stats{streamed_suffix}) "
                    f"over {batches} batches; pattern mining and graph checks deferred (streaming)"
                )
            else:
                notes = (
                    f"streamed scan: {total_findings} findings from tier-0 detectors over {batches} batches; "
                    f"statistical/pattern/ML deferred (streaming)"
                )
            if profile_note:
                notes = notes + "; " + profile_note
            if ml_flagged_total > ml_kept:
                notes = notes + f"; ml candidates capped at {ml_kept} of {ml_flagged_total} flagged"
            self.scans.complete(
                conn,
                scan_id=scan_id,
                completed_at=now(),
                profile=profile.to_dict(),
                notes=notes,
            )
            self.scans.save_quality(
                conn,
                scan_id=scan_id,
                observed_records=total_records,
                flagged_records=len(flagged),
            )
            if schema is not None:
                self.scans.save_schema(
                    conn, scan_id=scan_id, object_name=object_name, schema=schema.to_dict()
                )
            if self.audit is not None:
                if skip_statistical_pass:
                    deferred = sorted(d for d in DETECTOR_TIERS if d not in tier0)
                elif columnar_available:
                    deferred = ["pattern_break", "rare_combination", "duplicate_name", "missing_children"]
                    if not ran_streamed_stats:
                        deferred = ["numeric_outlier", "robust_outlier"] + deferred
                    if not used_sampled_ml:
                        deferred = deferred + ["ml_anomaly"]
                else:
                    deferred = [
                        "numeric_outlier", "robust_outlier", "rare_combination", "duplicate_name",
                        "missing_children", "pattern_break", "ml_anomaly",
                    ]
                detail = {
                    "row_count": total_records,
                    "batches": batches,
                    "finding_count": total_findings,
                    "deferred": deferred,
                    "checkpointed": True,
                    "ml_flagged_total": ml_flagged_total,
                    "ml_kept": ml_kept,
                }
                if skip_statistical_pass:
                    detail["delta_scan"] = True
                    detail["deferred_detectors"] = deferred
                self.audit.append(
                    conn,
                    event="scan_streamed",
                    actor="system",
                    scan_id=scan_id,
                    object_name=object_name,
                    detail=detail,
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
        src_key = source_key_from_name(source_name)
        with self.database.connect() as conn:
            self.scans.insert_running(
                conn, scan_id=scan_id, source_name=source_name, object_name=object_name,
                created_at=created, row_count=total, source_hash=source_hash, fields=fields,
                source_key=src_key,
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
