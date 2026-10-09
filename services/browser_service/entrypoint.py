"""Minimal Browser Service runtime for the C01 environment checkpoint.

Design references:
- doc/03_開發規則/01_環境文件.md, Development Environment
- doc/03_開發規則/02_資料夾結構.md, Project Structure
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse


SERVICE_NAME = "browser-executor-service"
DATA_DIR = Path(os.getenv("BROWSER_DATA_DIR", "/app/data"))

app = FastAPI(title="Browser Executor Service", version="0.1.0")


@app.get("/")
def root() -> dict[str, str]:
    """Identify the service without starting Chromium or a model call."""

    return {"service": SERVICE_NAME, "health": "/health"}


@app.get("/health")
def health() -> JSONResponse:
    """Report local readiness without opening a browser."""

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
                "browser_runtime": "not_started",
            },
        },
    )


def main() -> None:
    """Run the service using the configured bind address."""

    import uvicorn

    uvicorn.run(
        "services.browser_service.entrypoint:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        log_level=os.getenv("LOG_LEVEL", "INFO").lower(),
    )


if __name__ == "__main__":
    main()
