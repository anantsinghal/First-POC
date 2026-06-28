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
            profile = self.profiler.profile(dataset.records, dataset.fields)
            if coverage.grade == CoverageGrade.BLOCKED:
                candidates = []
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
                candidates = self.registry.detect(ctx)
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
                notes=f"{len(candidates)} candidate occurrences generated.",
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
                    },
                )
        return self.get_scan_detail(scan_id)

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
