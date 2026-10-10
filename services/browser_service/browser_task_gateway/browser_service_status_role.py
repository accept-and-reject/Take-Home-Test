"""Report Browser Service readiness without starting Chromium or a model."""

from __future__ import annotations

import os
from pathlib import Path

from services.browser_service.store import BrowserRunStore
from shared.settings import BrowserSettings


SERVICE_NAME = "browser-executor-service"


def report_browser_service_health(
    settings: BrowserSettings,
    store: BrowserRunStore,
) -> tuple[int, dict[str, object]]:
    storage_ready = settings.data_dir.is_dir() and os.access(
        settings.data_dir, os.W_OK
    )
    database_ready = storage_ready and store.is_healthy()
    runtime_ready = _browser_runtime_available()
    service_ready = storage_ready and database_ready
    if not service_ready:
        status = "unhealthy"
    elif not runtime_ready:
        status = "degraded"
    else:
        status = "healthy"
    return (
        200 if service_ready else 503,
        {
            "service": SERVICE_NAME,
            "status": status,
            "environment": settings.app_env,
            "checks": {
                "http": "healthy",
                "storage": "healthy" if storage_ready else "unhealthy",
                "database": "healthy" if database_ready else "unhealthy",
                "browser_runtime": "ready" if runtime_ready else "unavailable",
            },
        },
    )


def _browser_runtime_available() -> bool:
    configured_path = os.getenv("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if not configured_path:
        return False
    browser_root = Path(configured_path)
    try:
        return any(
            candidate.is_dir()
            for candidate in browser_root.glob("chromium-*")
        )
    except OSError:
        return False
