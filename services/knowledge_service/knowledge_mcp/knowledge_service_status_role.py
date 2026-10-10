"""Report Knowledge readiness separately from Browser integration status."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from services.knowledge_service.store import KnowledgeApplicationDataStore
from shared.settings import KnowledgeSettings


SERVICE_NAME = "knowledge-mcp-service"
BROWSER_HEALTH_TIMEOUT_SECONDS = 2


def report_knowledge_service_health(
    settings: KnowledgeSettings,
    store: KnowledgeApplicationDataStore,
) -> tuple[int, dict[str, object]]:
    storage_ready = settings.data_dir.is_dir() and os.access(
        settings.data_dir, os.W_OK
    )
    database_ready = storage_ready and store.is_healthy()
    knowledge_ready = storage_ready and database_ready
    browser_status, browser_detail = _browser_integration_status(settings)
    return (
        200 if knowledge_ready else 503,
        {
            "service": SERVICE_NAME,
            "status": "healthy" if knowledge_ready else "unhealthy",
            "environment": settings.app_env,
            "browser_integration": browser_status,
            "checks": {
                "http": "healthy",
                "storage": "healthy" if storage_ready else "unhealthy",
                "database": "healthy" if database_ready else "unhealthy",
                "browser_service": browser_detail,
            },
        },
    )


def _browser_integration_status(
    settings: KnowledgeSettings,
) -> tuple[str, str]:
    health_url = str(settings.browser_executor_base_url).rstrip("/") + "/health"
    try:
        with urllib.request.urlopen(
            health_url,
            timeout=BROWSER_HEALTH_TIMEOUT_SECONDS,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if response.status == 200 and payload.get("status") == "healthy":
            return "healthy", "healthy"
        return "degraded", "unhealthy"
    except (OSError, TimeoutError, ValueError, urllib.error.URLError):
        return "degraded", "unavailable"
