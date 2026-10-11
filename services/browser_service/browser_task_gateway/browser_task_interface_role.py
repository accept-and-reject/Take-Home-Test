"""Accept authenticated Browser Tasks and return their persisted state."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from services.browser_service.browser_task_gateway.browser_task_validation_role import (
    validate_task_schema,
    validate_task_scope,
)
from services.browser_service.store import BrowserRunStore
from shared.contracts import (
    BrowserRun,
    BrowserRunStatus,
    BrowserTaskSource,
    ExecutionRequest,
    ExecutionValidationStatus,
)
from shared.settings import BrowserSettings


CallerKind = Literal["demo", "service"]


def authenticate_browser_caller(
    authorization: str | None,
    settings: BrowserSettings,
) -> CallerKind | None:
    if authorization is None:
        return None
    scheme, separator, credential = authorization.partition(" ")
    if separator != " " or scheme.casefold() != "bearer":
        return None
    if secrets.compare_digest(credential, settings.browser_executor_token):
        return "service"
    if secrets.compare_digest(credential, settings.demo_api_token):
        return "demo"
    return None


def accept_browser_task(
    payload: object,
    user_instruction: str,
    caller: CallerKind,
    settings: BrowserSettings,
    store: BrowserRunStore,
    *,
    execution_policy_reason: str | None = None,
) -> tuple[int, dict[str, Any]]:
    schema_result = validate_task_schema(payload)
    if not schema_result.accepted or schema_result.task is None:
        return 422, {
            "status": "rejected",
            "validation_phase": "schema",
            "reasons": schema_result.reasons,
            "execution_request_id": None,
            "browser_run": None,
        }

    task = schema_result.task
    scope_result = validate_task_scope(task, settings)
    reasons = list(scope_result.reasons)
    if caller == "demo" and task.task_source is not BrowserTaskSource.DIRECT:
        reasons.append("demo credentials may only submit direct tasks")
    if caller == "service" and task.task_source is not BrowserTaskSource.ACTION_ITEM:
        reasons.append("service credentials require an action_item task")
    has_validation_rejection = bool(reasons)
    if execution_policy_reason is not None:
        reasons.append(execution_policy_reason)

    now = datetime.now(UTC)
    execution_request = ExecutionRequest(
        execution_request_id=uuid4(),
        browser_task_id=task.browser_task_id,
        user_instruction=user_instruction,
        validation_status=(
            ExecutionValidationStatus.REJECTED
            if reasons
            else ExecutionValidationStatus.ACCEPTED
        ),
        rejection_reason="; ".join(reasons) if reasons else None,
        requested_at=now,
    )
    browser_run = None
    if not reasons:
        browser_run = BrowserRun(
            run_id=uuid4(),
            execution_request_id=execution_request.execution_request_id,
            status=BrowserRunStatus.PENDING,
            visited_page_count=0,
            error_summary=None,
            started_at=now,
            completed_at=None,
        )
    store.save_task_submission(task, execution_request, browser_run)
    if has_validation_rejection:
        status_code = 422
    elif execution_policy_reason is not None:
        status_code = 429
    else:
        status_code = 202
    return (
        status_code,
        return_browser_task_result(
            execution_request.execution_request_id,
            store,
        ),
    )


def return_browser_task_result(
    execution_request_id: UUID,
    store: BrowserRunStore,
) -> dict[str, Any]:
    request = store.get_execution_request(execution_request_id)
    if request is None:
        return {
            "status": "not_found",
            "execution_request_id": str(execution_request_id),
        }
    task = store.get_browser_task(request.browser_task_id)
    run = store.get_browser_run_by_execution_request(execution_request_id)
    logs = store.list_action_logs(run.run_id) if run else []
    findings = store.list_research_findings(run.run_id) if run else []
    return {
        "status": (
            run.status.value if run else request.validation_status.value
        ),
        "browser_task": task.model_dump(mode="json") if task else None,
        "execution_request": request.model_dump(mode="json"),
        "browser_run": run.model_dump(mode="json") if run else None,
        "findings": [item.model_dump(mode="json") for item in findings],
        "action_logs": [item.model_dump(mode="json") for item in logs],
    }
