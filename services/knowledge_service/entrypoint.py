"""Minimal Knowledge Service runtime for the C01 environment checkpoint.

Design references:
- doc/03_開發規則/01_環境文件.md, Development Environment
- doc/03_開發規則/02_資料夾結構.md, Project Structure
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse


SERVICE_NAME = "knowledge-mcp-service"
DATA_DIR = Path(os.getenv("KNOWLEDGE_DATA_DIR", "/app/data"))

app = FastAPI(title="Knowledge and Remote MCP Service", version="0.1.0")


@app.get("/")
def root() -> dict[str, str]:
    """Identify the service without triggering models or dependent services."""

    return {"service": SERVICE_NAME, "health": "/health"}


@app.get("/health")
def health() -> JSONResponse:
    """Report only local HTTP and data-directory readiness."""

    storage_ready = DATA_DIR.is_dir() and os.access(DATA_DIR, os.W_OK)
    status = "healthy" if storage_ready else "unhealthy"
    return JSONResponse(
        status_code=200 if storage_ready else 503,
        content={
            "service": SERVICE_NAME,
            "status": status,
            "environment": os.getenv("APP_ENV", "development"),
            "checks": {
                "http": "healthy",
                "storage": "healthy" if storage_ready else "unhealthy",
            },
        },
    )


def main() -> None:
    """Run the service using the configured bind address."""

    import uvicorn

    uvicorn.run(
        "services.knowledge_service.entrypoint:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        log_level=os.getenv("LOG_LEVEL", "INFO").lower(),
    )


if __name__ == "__main__":
    main()
