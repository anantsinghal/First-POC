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
)
from ..policy.field_policy import DEFAULT_POLICY
from ..profiling.profiler import Profiler
from ..services.audit_service import AuditService
from ..services.dashboard_service import DashboardService
from ..services.finding_service import FindingService
from ..services.scan_service import ScanService


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


def build_container(*, data_dir: Path, root: Path) -> Container:
    """Replicate the exact wiring currently in main.py."""
    database = Database(data_dir / "scanner.db")
    profiler = Profiler()
    registry = build_default_registry()
    grader = CoverageGrader(DEFAULT_POLICY, requirements=registry.requirements())
    scans = ScanRepository()
    coverage_repo = CoverageRepository()
    findings = FindingRepository()
    feedback = FeedbackRepository()
    audit = AuditRepository()

    scan_service = ScanService(
        database=database,
        profiler=profiler,
        grader=grader,
        registry=registry,
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
    )
    dashboard_service = DashboardService(
        database=database,
        scans=scans,
        findings=findings,
        scan_service=scan_service,
    )
    audit_service = AuditService(database=database, audit=audit)

    return Container(
        root=root,
        demo_file=root / "data" / "demo_opportunities.csv",
        database=database,
        scan_service=scan_service,
        finding_service=finding_service,
        dashboard_service=dashboard_service,
        audit_service=audit_service,
    )


def get_container(request: Request) -> Container:
    """FastAPI dependency to retrieve the container from app.state."""
    return request.app.state.container
