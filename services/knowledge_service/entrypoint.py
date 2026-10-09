"""Knowledge Service runtime and local persistence health endpoint.

Design references:
- doc/03_開發規則/01_環境文件.md, Development Environment
- doc/03_開發規則/02_資料夾結構.md, Project Structure
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from services.knowledge_service.store import KnowledgeApplicationDataStore
from shared.settings import KnowledgeSettings


SERVICE_NAME = "knowledge-mcp-service"
SETTINGS = KnowledgeSettings.from_env()
STORE = KnowledgeApplicationDataStore(SETTINGS.data_dir)
STORE.initialize()

app = FastAPI(title="Knowledge and Remote MCP Service", version="0.1.0")


@app.get("/")
def root() -> dict[str, str]:
    """Identify the service without triggering models or dependent services."""

    return {"service": SERVICE_NAME, "health": "/health"}


@app.get("/health")
def health() -> JSONResponse:
    """Report only local HTTP and data-directory readiness."""

    storage_ready = SETTINGS.data_dir.is_dir() and os.access(
        SETTINGS.data_dir, os.W_OK
    )
    database_ready = storage_ready and STORE.is_healthy()
    status = "healthy" if database_ready else "unhealthy"
    return JSONResponse(
        status_code=200 if database_ready else 503,
        content={
            "service": SERVICE_NAME,
            "status": status,
            "environment": os.getenv("APP_ENV", "development"),
            "checks": {
                "http": "healthy",
                "storage": "healthy" if storage_ready else "unhealthy",
                "database": "healthy" if database_ready else "unhealthy",
            },
        },
    )


def main() -> None:
    """Run the service using the configured bind address."""

    import uvicorn

    uvicorn.run(
        "services.knowledge_service.entrypoint:app",
        host=SETTINGS.host,
        port=SETTINGS.port,
        log_level=SETTINGS.log_level.lower(),
    )


if __name__ == "__main__":
    main()
