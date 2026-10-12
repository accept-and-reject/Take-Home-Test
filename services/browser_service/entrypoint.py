"""Authenticated API for validating, executing, and returning Browser Tasks."""

from __future__ import annotations

from datetime import UTC, datetime
from threading import BoundedSemaphore
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
    accept_browser_task,
    authenticate_browser_token,
    return_browser_task_result,
)
from services.browser_service.store import BrowserRunStore
from services.browser_service.workflow import execute_browser_task
from shared.settings import BrowserSettings


SETTINGS = BrowserSettings.from_env()
STORE = BrowserRunStore(SETTINGS.data_dir)
STORE.initialize()
RUN_SLOTS = BoundedSemaphore(SETTINGS.max_concurrent_browser_runs)

app = FastAPI(title="Browser Executor Service", version="0.1.0")


class BrowserTaskSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    user_instruction: str = Field(min_length=1)
    browser_task: dict[str, Any]


def _authenticate_browser_request(authorization: str | None) -> None:
    if not authenticate_browser_token(authorization, SETTINGS):
        raise HTTPException(
            status_code=401,
            detail="invalid or missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )


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
    _authenticate_browser_request(authorization)
    slot_acquired = RUN_SLOTS.acquire(blocking=False)
    try:
        execution_policy_reason = None
        if not slot_acquired:
            execution_policy_reason = "maximum concurrent browser runs reached"
        else:
            today_utc = datetime.now(UTC).replace(
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )
            if (
                STORE.count_started_browser_runs_since(today_utc)
                >= SETTINGS.max_daily_browser_runs
            ):
                execution_policy_reason = "daily browser run limit reached"
        status_code, content = accept_browser_task(
            submission.browser_task,
            submission.user_instruction,
            SETTINGS,
            STORE,
            execution_policy_reason=execution_policy_reason,
        )
        if status_code == 202:
            browser_run = content.get("browser_run") or {}
            run_id = UUID(browser_run["run_id"])
            execute_browser_task(run_id, SETTINGS, STORE)
            execution_request = content.get("execution_request") or {}
            content = return_browser_task_result(
                UUID(execution_request["execution_request_id"]),
                STORE,
            )
            status_code = 200
        return JSONResponse(status_code=status_code, content=content)
    finally:
        if slot_acquired:
            RUN_SLOTS.release()


@app.get("/browser-tasks/{execution_request_id}")
def get_browser_task_result(
    execution_request_id: UUID,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    _authenticate_browser_request(authorization)
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
