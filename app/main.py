from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
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

# Throttle auth_failed audit rows: at most one per path per 10 seconds.
_last_auth_failed: dict[str, float] = {}


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

    cors_origins = os.environ.get("SCANNER_CORS_ORIGINS")
    if cors_origins:
        origins = [o.strip() for o in cors_origins.split(",") if o.strip()]
        if origins:
            app.add_middleware(
                CORSMiddleware,
                allow_origins=origins,
                allow_headers=["X-API-Key", "Content-Type"],
                allow_methods=["GET", "POST"],
            )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.middleware("http")
    async def auth_failed_audit(request: Request, call_next):
        """On 401 from api_key_guard, append a throttled auth_failed audit (never logs keys)."""
        response = await call_next(request)
        if response.status_code == 401 and getattr(request.state, "auth_failed", False):
            try:
                path = request.url.path
                now = time.monotonic()
                last = _last_auth_failed.get(path, 0.0)
                if now - last >= 10.0:
                    _last_auth_failed[path] = now
                    request.app.state.container.audit_service.append(
                        event="auth_failed",
                        actor="unknown",
                        detail={"path": path},
                    )
            except Exception:
                # Auditing must never break the 401 response.
                pass
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
