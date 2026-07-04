from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import Request

from ..coverage.grader import CoverageGrader
from ..detection.registry import build_default_registry
from ..persistence.database import Database
from ..persistence.repositories import (
    AuditRepository,
    CoverageRepository,
    FeedbackRepository,
    FindingRepository,
    ScanRepository,
    SyncRepository,
)
from ..planning.planner import ScanPlanner
from ..policy.field_policy import DEFAULT_POLICY
from ..profiling.profiler import Profiler
from ..services.audit_service import AuditService
from ..services.dashboard_service import DashboardService
from ..services.drift_service import DriftService
from ..services.finding_service import FindingService
from ..services.metrics_service import MetricsService
from ..services.quality_service import QualityService
from ..services.ranking_service import RankingService
from ..services.retention_service import RetentionService
from ..services.scan_service import ScanService
from ..services.sync_service import SyncService


@dataclass
class Container:
    """Holds all wired dependencies for the API layer to depend on (via app.state)."""

    root: Path
    demo_file: Path
    database: Database
    scan_service: ScanService
    finding_service: FindingService
    dashboard_service: DashboardService
    audit_service: AuditService
    retention_service: RetentionService
    metrics_service: MetricsService
    quality_service: QualityService
    sync_service: SyncService
    ranking_service: RankingService
    drift_service: DriftService


def build_container(*, data_dir: Path, root: Path) -> Container:
    """Replicate the exact wiring currently in main.py."""
    database = Database(data_dir / "scanner.db")
    profiler = Profiler()
    registry = build_default_registry()
    planner = ScanPlanner()
    grader = CoverageGrader(DEFAULT_POLICY, requirements=registry.requirements())
    scans = ScanRepository()
    coverage_repo = CoverageRepository()
    findings = FindingRepository()
    feedback = FeedbackRepository()
    audit = AuditRepository()
    sync = SyncRepository()
    ranking_service = RankingService(database=database, feedback=feedback)

    scan_service = ScanService(
        database=database,
        profiler=profiler,
        grader=grader,
        registry=registry,
        planner=planner,
        scans=scans,
        coverage_repo=coverage_repo,
        findings=findings,
        policy=DEFAULT_POLICY,
        audit=audit,
    )
    finding_service = FindingService(
        database=database,
        findings=findings,
        feedback=feedback,
        audit=audit,
        ranker=ranking_service,
    )
    dashboard_service = DashboardService(
        database=database,
        scans=scans,
        findings=findings,
        scan_service=scan_service,
    )
    audit_service = AuditService(database=database, audit=audit)
    retention_service = RetentionService(database=database, scans=scans, audit=audit)
    metrics_service = MetricsService(
        database=database,
        findings=findings,
        feedback=feedback,
        ranker=ranking_service,
    )
    quality_service = QualityService(
        database=database,
        scans=scans,
        coverage_repo=coverage_repo,
        findings=findings,
    )
    sync_service = SyncService(
        database=database,
        scan_service=scan_service,
        sync=sync,
        findings=findings,
        audit=audit,
    )
    drift_service = DriftService(database=database, scans=scans, audit=audit)

    return Container(
        root=root,
        demo_file=root / "data" / "demo_opportunities.csv",
        database=database,
        scan_service=scan_service,
        finding_service=finding_service,
        dashboard_service=dashboard_service,
        audit_service=audit_service,
        retention_service=retention_service,
        metrics_service=metrics_service,
        quality_service=quality_service,
        sync_service=sync_service,
        ranking_service=ranking_service,
        drift_service=drift_service,
    )


def get_container(request: Request) -> Container:
    """FastAPI dependency to retrieve the container from app.state."""
    return request.app.state.container
