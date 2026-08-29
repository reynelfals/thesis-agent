"""FastAPI entry point for the non-blocking, read-only audit dashboard."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from thesis.alpaca.client import PaperClient
from thesis.config import ConfigError, load_settings
from thesis.store import ThesisStore
from thesis.web.dashboard import Dashboard, sanitize_public_payload

STATIC_DIR = Path(__file__).resolve().parent / "static"


class _ConfigurationErrorDashboard:
    def __init__(self, interval: float) -> None:
        self._snapshot = {
            "status": "error",
            "generated_at": None,
            "last_attempt_at": None,
            "refresh_interval_seconds": interval,
            "error": "Dashboard configuration is invalid.",
            "execution_enabled": False,
            "banner": "Dashboard unavailable. No broker write was attempted.",
            "readiness": None,
            "performance": None,
            "positions": {"tracked": [], "live_legs": [], "has_unmanaged": False},
            "cycles": [],
        }

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def snapshot(self) -> dict[str, Any]:
        return dict(self._snapshot)


def _refresh_interval() -> float:
    try:
        return max(float(os.getenv("THESIS_DASHBOARD_REFRESH_SECONDS", "60")), 1.0)
    except ValueError:
        return 60.0


def _default_dashboard() -> Dashboard | _ConfigurationErrorDashboard:
    interval = _refresh_interval()
    try:
        settings = load_settings()
        return Dashboard(
            settings=settings,
            store=ThesisStore(settings.db_path),
            client=PaperClient(settings),
            refresh_interval_seconds=interval,
        )
    except (ConfigError, ValueError):
        return _ConfigurationErrorDashboard(interval)


def create_app(dashboard: Any | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        if application.state.dashboard is None:
            application.state.dashboard = _default_dashboard()
        application.state.dashboard.start()
        try:
            yield
        finally:
            application.state.dashboard.stop()

    application = FastAPI(
        title="Thesis paper trading audit",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    application.state.dashboard = dashboard

    @application.get("/api/dashboard")
    async def get_dashboard() -> dict[str, Any]:
        # This is only an in-memory copy under a short lock; broker I/O happens
        # exclusively in Dashboard's daemon refresh thread.
        return sanitize_public_payload(application.state.dashboard.snapshot())

    @application.get("/api/health")
    async def get_health() -> dict[str, Any]:
        snapshot = sanitize_public_payload(application.state.dashboard.snapshot())
        return {
            "ok": snapshot["status"] in {"ready", "stale"},
            "status": snapshot["status"],
            "generated_at": snapshot["generated_at"],
        }

    @application.get("/", response_class=HTMLResponse)
    async def index() -> Any:
        path = STATIC_DIR / "index.html"
        if path.is_file():
            return FileResponse(path)
        return HTMLResponse(
            "<!doctype html><title>Thesis</title>"
            "<main><h1>Thesis paper trading audit</h1>"
            "<p>The dashboard frontend has not been built yet.</p></main>"
        )

    @application.get("/brief", response_class=HTMLResponse)
    async def brief() -> Any:
        path = STATIC_DIR / "brief.html"
        if path.is_file():
            return FileResponse(path)
        return HTMLResponse(
            "<!doctype html><title>Thesis brief</title>"
            "<main><h1>Thesis submission brief</h1>"
            "<p>The brief frontend has not been built yet.</p></main>"
        )

    @application.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> Response:
        return Response(status_code=204)

    application.mount(
        "/assets",
        StaticFiles(directory=STATIC_DIR),
        name="assets",
    )
    return application


app = create_app()