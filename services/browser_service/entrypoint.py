"""Authenticated Browser Task API without automatic browser execution."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from services.browser_service.browser_task_gateway.browser_service_status_role import (
    SERVICE_NAME,
    report_browser_service_health,
)
from services.browser_service.browser_task_gateway.browser_task_interface_role import (
    CallerKind,
    accept_browser_task,
    authenticate_browser_caller,
    return_browser_task_result,
)
from services.browser_service.store import BrowserRunStore
from shared.settings import BrowserSettings


SETTINGS = BrowserSettings.from_env()
STORE = BrowserRunStore(SETTINGS.data_dir)
STORE.initialize()

app = FastAPI(title="Browser Executor Service", version="0.1.0")


class BrowserTaskSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    user_instruction: str = Field(min_length=1)
    browser_task: dict[str, Any]


def _authenticated_caller(authorization: str | None) -> CallerKind:
    caller = authenticate_browser_caller(authorization, SETTINGS)
    if caller is None:
        raise HTTPException(
            status_code=401,
            detail="invalid or missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return caller


@app.get("/")
def root() -> dict[str, str]:
    """Identify the service without starting Chromium or a model call."""

    return {
        "service": SERVICE_NAME,
        "health": "/health",
        "browser_tasks": "/browser-tasks",
    }


@app.get("/health")
def health() -> JSONResponse:
    """Report local readiness without opening a browser."""

    status_code, content = report_browser_service_health(SETTINGS, STORE)
    return JSONResponse(
        status_code=status_code,
        content=content,
    )


@app.post("/browser-tasks")
def submit_browser_task(
    submission: BrowserTaskSubmission,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    caller = _authenticated_caller(authorization)
    status_code, content = accept_browser_task(
        submission.browser_task,
        submission.user_instruction,
        caller,
        SETTINGS,
        STORE,
    )
    return JSONResponse(status_code=status_code, content=content)


@app.get("/browser-tasks/{execution_request_id}")
def get_browser_task_result(
    execution_request_id: UUID,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    _authenticated_caller(authorization)
    content = return_browser_task_result(execution_request_id, STORE)
    return JSONResponse(
        status_code=404 if content["status"] == "not_found" else 200,
        content=content,
    )


def main() -> None:
    """Run the service using the configured bind address."""

    import uvicorn

    uvicorn.run(
        "services.browser_service.entrypoint:app",
        host=SETTINGS.host,
        port=SETTINGS.port,
        log_level=SETTINGS.log_level.lower(),
    )


if __name__ == "__main__":
    main()
