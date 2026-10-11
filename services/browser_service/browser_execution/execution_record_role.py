"""Persist auditable browser actions and final Browser Run status.

Design: ``doc/02_系統設計/03_內部架構圖.md`` — ExecutionRecordRole.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from services.browser_service.store import BrowserRunStore
from shared.contracts import (
    BrowserActionLog,
    BrowserActionStatus,
    BrowserActionType,
    BrowserRun,
    BrowserRunStatus,
    FindingStatus,
)


def record_browser_action(
    run_id: UUID,
    action_type: BrowserActionType,
    status: BrowserActionStatus,
    store: BrowserRunStore,
    *,
    target_url: str | None = None,
    reason: str | None = None,
    detail: str | None = None,
) -> BrowserActionLog:
    if store.get_browser_run(run_id) is None:
        raise ValueError(f"browser run {run_id} does not exist")
    log = BrowserActionLog(
        action_log_id=uuid4(),
        run_id=run_id,
        sequence=store.next_action_sequence(run_id),
        action_type=action_type,
        target_url=target_url,
        reason=reason,
        status=status,
        detail=detail,
        occurred_at=datetime.now(UTC),
    )
    store.save_action_log(log)
    return log


def finalize_browser_run(
    run_id: UUID,
    visited_page_count: int,
    store: BrowserRunStore,
    *,
    error_summary: str | None,
) -> BrowserRun:
    run = store.get_browser_run(run_id)
    if run is None:
        raise ValueError(f"browser run {run_id} does not exist")
    findings = store.list_research_findings(run_id)
    found_count = sum(
        finding.status is FindingStatus.FOUND for finding in findings
    )
    failed_count = sum(
        finding.status is FindingStatus.FAILED for finding in findings
    )
    if findings and found_count == len(findings):
        final_status = BrowserRunStatus.COMPLETED
    elif found_count > 0:
        final_status = BrowserRunStatus.PARTIAL
    elif failed_count > 0:
        final_status = BrowserRunStatus.FAILED
    else:
        final_status = BrowserRunStatus.PARTIAL
    finalized = run.model_copy(
        update={
            "status": final_status,
            "visited_page_count": visited_page_count,
            "error_summary": error_summary,
            "completed_at": datetime.now(UTC),
        }
    )
    store.save_browser_run(finalized)
    return finalized
