from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.staticfiles import StaticFiles

from app.api.container import build_container
from app.api.routes_admin import router as admin_router
from app.api.routes_audit import router as audit_router
from app.api.routes_dashboard import router as dashboard_router
from app.api.routes_findings import router as findings_router
from app.api.routes_learning import router as learning_router
from app.api.routes_metrics import router as metrics_router
from app.api.routes_salesforce import router as salesforce_router
from app.api.routes_scans import router as scans_router
from app.api.routes_system import router as system_router
from app.api.security import api_key_guard


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("SCANNER_DATA_DIR", ROOT / "data"))


def create_app() -> FastAPI:
    """Pure factory that wires via container and includes routers. Module level app = create_app() for uvicorn/tests."""
    container = build_container(data_dir=DATA_DIR, root=ROOT)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        container.database.setup()
        yield

    app = FastAPI(title="Salesforce Data Quality Scanner POC", version="0.1.0", lifespan=lifespan)
    app.state.container = container
    app.mount("/static", StaticFiles(directory=ROOT / "app" / "static"), name="static")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    # System router stays open: /api/health liveness + UI shell pages (/, /tutorial).
    # Data-bearing routers require api_key_guard when SCANNER_API_KEY is set.
    app.include_router(system_router)
    protected = [Depends(api_key_guard)]
    app.include_router(scans_router, dependencies=protected)
    app.include_router(findings_router, dependencies=protected)
    app.include_router(salesforce_router, dependencies=protected)
    app.include_router(dashboard_router, dependencies=protected)
    app.include_router(audit_router, dependencies=protected)
    app.include_router(admin_router, dependencies=protected)
    app.include_router(metrics_router, dependencies=protected)
    app.include_router(learning_router, dependencies=protected)

    return app


app = create_app()
