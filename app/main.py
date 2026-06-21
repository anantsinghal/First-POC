from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.container import build_container
from app.api.routes_dashboard import router as dashboard_router
from app.api.routes_findings import router as findings_router
from app.api.routes_salesforce import router as salesforce_router
from app.api.routes_scans import router as scans_router
from app.api.routes_system import router as system_router


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

    app.include_router(system_router)
    app.include_router(scans_router)
    app.include_router(findings_router)
    app.include_router(salesforce_router)
    app.include_router(dashboard_router)

    return app


app = create_app()
