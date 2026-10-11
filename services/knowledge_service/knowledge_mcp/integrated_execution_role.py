"""Build and execute Browser Tasks from traceable ActionItems.

Design source: ``doc/02_系統設計/05_功能文件.md``,
``IntegratedExecutionRole``.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from services.knowledge_service.store import KnowledgeApplicationDataStore
from shared.contracts import (
    ActionItem,
    ActionKind,
    ActionStatus,
    BrowserActionLog,
    BrowserRun,
    BrowserTask,
    BrowserTaskSource,
    ContentChunk,
    ExecutionRequest,
    ResearchFinding,
    ResearchTopic,
)
from shared.settings import KnowledgeSettings


EXECUTABLE_ACTION_STATUSES = {ActionStatus.OPEN, ActionStatus.IN_PROGRESS}
EXPECTED_RESEARCH_TOPICS = set(ResearchTopic)
DEFAULT_MAX_PAGES = 5


@dataclass(frozen=True)
class ActionItemBrowserTask:
    """Validated Knowledge-owned context for one Browser execution."""

    action_item: ActionItem
    evidence_chunks: tuple[ContentChunk, ...]
    browser_task: BrowserTask


class ActionItemExecutionRejected(ValueError):
    """Describe why an ActionItem cannot become a BrowserTask."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def build_task_from_action_item(
    action_item_id: UUID,
    settings: KnowledgeSettings,
    store: KnowledgeApplicationDataStore,
) -> ActionItemBrowserTask:
    """Validate one ActionItem and create a self-contained BrowserTask."""

    item = store.get_action_item(action_item_id)
    if item is None:
        raise ActionItemExecutionRejected(
            "action_item_not_found",
            "The selected ActionItem does not exist.",
        )
    if item.action_kind is not ActionKind.COMPANY_RESEARCH:
        raise ActionItemExecutionRejected(
            "action_item_not_executable",
            "Only company_research ActionItems can start a Browser Task.",
        )
    if item.status not in EXECUTABLE_ACTION_STATUSES:
        raise ActionItemExecutionRejected(
            "action_item_status_not_executable",
            f"ActionItem status {item.status.value!r} cannot be executed.",
        )
    if not item.target_company or not item.allowed_domain:
        raise ActionItemExecutionRejected(
            "action_item_scope_missing",
            "The ActionItem is missing its company or official-domain scope.",
        )
    if item.target_company.casefold() != settings.company_name.casefold():
        raise ActionItemExecutionRejected(
            "action_item_scope_mismatch",
            "The ActionItem company is outside the configured company scope.",
        )
    if _normalized_domain(item.allowed_domain) != _normalized_domain(
        settings.allowed_domain
    ):
        raise ActionItemExecutionRejected(
            "action_item_scope_mismatch",
            "The ActionItem domain is outside the configured official domain.",
        )
    supplied_topics = set(item.requested_topics)
    if (
        supplied_topics != EXPECTED_RESEARCH_TOPICS
        or len(item.requested_topics) != len(EXPECTED_RESEARCH_TOPICS)
    ):
        raise ActionItemExecutionRejected(
            "action_item_scope_missing",
            "The ActionItem must request each supported research topic once.",
        )

    evidence_chunks = tuple(
        chunk
        for chunk_id in item.evidence_chunk_ids
        if (chunk := store.get_content_chunk(chunk_id)) is not None
    )
    if not evidence_chunks or len(evidence_chunks) != len(
        item.evidence_chunk_ids
    ):
        raise ActionItemExecutionRejected(
            "action_item_evidence_missing",
            "The ActionItem does not have a complete set of traceable evidence.",
        )

    task = BrowserTask(
        browser_task_id=uuid4(),
        task_source=BrowserTaskSource.ACTION_ITEM,
        source_action_item_id=item.action_item_id,
        target_company=item.target_company,
        start_url=settings.start_url,
        allowed_domain=item.allowed_domain,
        requested_topics=item.requested_topics,
        max_pages=DEFAULT_MAX_PAGES,
        created_at=datetime.now(UTC),
    )
    return ActionItemBrowserTask(
        action_item=item,
        evidence_chunks=evidence_chunks,
        browser_task=task,
    )


def call_browser_executor(
    context: ActionItemBrowserTask,
    settings: KnowledgeSettings,
) -> dict[str, Any]:
    """Synchronously submit one BrowserTask with bounded service credentials."""

    endpoint = (
        str(settings.browser_executor_base_url).rstrip("/") + "/browser-tasks"
    )
    payload = {
        "user_instruction": (
            "Execute the explicitly selected company research ActionItem: "
            f"{context.action_item.description}"
        ),
        "browser_task": context.browser_task.model_dump(mode="json"),
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.browser_executor_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=settings.browser_request_timeout_seconds,
        ) as response:
            result = _validated_browser_result(
                _read_json_object(response),
                context,
            )
    except urllib.error.HTTPError as exc:
        result = _read_http_error(exc)
        if exc.code in {401, 403}:
            return _integration_error(
                "browser_service_authentication_failed",
                "Browser Service rejected the service credential.",
                context,
            )
        if exc.code == 422:
            return {
                **_context_payload(context),
                **result,
            }
        if exc.code == 429:
            return {
                **_context_payload(context),
                **result,
                "browser_service_status": result.get("status"),
                "status": "browser_service_rate_limited",
                "error": {
                    "code": "browser_service_rate_limited",
                    "message": (
                        "Browser Service rejected the run because an execution "
                        "limit was reached."
                    ),
                },
            }
        return _integration_error(
            "browser_service_unavailable",
            f"Browser Service returned HTTP {exc.code}.",
            context,
        )
    except (TimeoutError, socket.timeout):
        return _integration_error(
            "browser_service_unavailable",
            "Browser Service did not finish before the configured timeout.",
            context,
        )
    except (OSError, urllib.error.URLError):
        return _integration_error(
            "browser_service_unavailable",
            "Browser Service could not be reached.",
            context,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return _integration_error(
            "browser_service_invalid_response",
            "Browser Service returned an invalid response.",
            context,
        )

    return {
        **_context_payload(context),
        **result,
    }


def _context_payload(context: ActionItemBrowserTask) -> dict[str, Any]:
    return {
        "action_item": context.action_item.model_dump(mode="json"),
    }


def _integration_error(
    code: str,
    message: str,
    context: ActionItemBrowserTask,
) -> dict[str, Any]:
    return {
        "status": code,
        "error": {
            "code": code,
            "message": message,
        },
        **_context_payload(context),
        "browser_task": context.browser_task.model_dump(mode="json"),
        "execution_request": None,
        "browser_run": None,
        "findings": [],
        "action_logs": [],
    }


def _read_json_object(response: Any) -> dict[str, Any]:
    payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Browser Service response must be a JSON object")
    return payload


def _read_http_error(error: urllib.error.HTTPError) -> dict[str, Any]:
    try:
        return _read_json_object(error)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return {
            "status": "browser_service_request_failed",
            "error": {
                "code": "browser_service_request_failed",
                "message": f"Browser Service returned HTTP {error.code}.",
            },
        }


def _validated_browser_result(
    payload: dict[str, Any],
    context: ActionItemBrowserTask,
) -> dict[str, Any]:
    task = BrowserTask.model_validate(payload.get("browser_task"))
    request = ExecutionRequest.model_validate(payload.get("execution_request"))
    run = BrowserRun.model_validate(payload.get("browser_run"))
    findings = [
        ResearchFinding.model_validate(item)
        for item in payload.get("findings", [])
    ]
    logs = [
        BrowserActionLog.model_validate(item)
        for item in payload.get("action_logs", [])
    ]

    expected_task = context.browser_task
    if task != expected_task:
        raise ValueError("Browser Service returned a different BrowserTask")
    if request.browser_task_id != task.browser_task_id:
        raise ValueError("ExecutionRequest does not reference the BrowserTask")
    if run.execution_request_id != request.execution_request_id:
        raise ValueError("BrowserRun does not reference the ExecutionRequest")
    if any(item.run_id != run.run_id for item in findings):
        raise ValueError("ResearchFinding does not reference the BrowserRun")
    if any(item.run_id != run.run_id for item in logs):
        raise ValueError("BrowserActionLog does not reference the BrowserRun")

    return {
        "status": run.status.value,
        "browser_task": task.model_dump(mode="json"),
        "execution_request": request.model_dump(mode="json"),
        "browser_run": run.model_dump(mode="json"),
        "findings": [item.model_dump(mode="json") for item in findings],
        "action_logs": [item.model_dump(mode="json") for item in logs],
    }


def _normalized_domain(value: str) -> str:
    return value.strip().rstrip(".").casefold()
