"""SQLite persistence owned exclusively by the Browser Executor service."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from shared.contracts import (
    BrowserActionLog,
    BrowserRun,
    BrowserTask,
    ExecutionRequest,
    ResearchFinding,
)
from shared.sqlite_store import SQLiteStore


BROWSER_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS browser_tasks (
        browser_task_id TEXT PRIMARY KEY,
        task_source TEXT NOT NULL,
        source_action_item_id TEXT,
        target_company TEXT NOT NULL,
        start_url TEXT NOT NULL,
        allowed_domain TEXT NOT NULL,
        requested_topics TEXT NOT NULL,
        max_pages INTEGER NOT NULL CHECK (max_pages BETWEEN 1 AND 5),
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS execution_requests (
        execution_request_id TEXT PRIMARY KEY,
        browser_task_id TEXT NOT NULL,
        user_instruction TEXT NOT NULL,
        validation_status TEXT NOT NULL,
        rejection_reason TEXT,
        requested_at TEXT NOT NULL,
        FOREIGN KEY (browser_task_id) REFERENCES browser_tasks(browser_task_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS browser_runs (
        run_id TEXT PRIMARY KEY,
        execution_request_id TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL,
        visited_page_count INTEGER NOT NULL CHECK (visited_page_count >= 0),
        error_summary TEXT,
        started_at TEXT NOT NULL,
        completed_at TEXT,
        FOREIGN KEY (execution_request_id)
            REFERENCES execution_requests(execution_request_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS browser_action_logs (
        action_log_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        sequence INTEGER NOT NULL CHECK (sequence >= 1),
        action_type TEXT NOT NULL,
        target_url TEXT,
        reason TEXT,
        status TEXT NOT NULL,
        detail TEXT,
        occurred_at TEXT NOT NULL,
        FOREIGN KEY (run_id) REFERENCES browser_runs(run_id) ON DELETE CASCADE,
        UNIQUE (run_id, sequence)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS research_findings (
        finding_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        topic TEXT NOT NULL,
        status TEXT NOT NULL,
        summary TEXT,
        source_urls TEXT NOT NULL,
        message TEXT,
        FOREIGN KEY (run_id) REFERENCES browser_runs(run_id) ON DELETE CASCADE,
        UNIQUE (run_id, topic)
    )
    """,
)


def _payload(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")


class BrowserRunStore:
    """Persist Browser entities without access to Knowledge service data."""

    def __init__(self, data_dir: Path) -> None:
        self.database = SQLiteStore(data_dir / "browser.sqlite3")

    @property
    def database_path(self) -> Path:
        return self.database.database_path

    def initialize(self) -> None:
        self.database.initialize(BROWSER_SCHEMA)

    def is_healthy(self) -> bool:
        return self.database.is_healthy()

    def save_browser_task(self, task: BrowserTask) -> None:
        data = _payload(task)
        self.database.execute(
            """
            INSERT INTO browser_tasks (
                browser_task_id, task_source, source_action_item_id,
                target_company, start_url, allowed_domain, requested_topics,
                max_pages, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(browser_task_id) DO UPDATE SET
                task_source=excluded.task_source,
                source_action_item_id=excluded.source_action_item_id,
                target_company=excluded.target_company,
                start_url=excluded.start_url,
                allowed_domain=excluded.allowed_domain,
                requested_topics=excluded.requested_topics,
                max_pages=excluded.max_pages,
                created_at=excluded.created_at
            """,
            (
                data["browser_task_id"], data["task_source"],
                data["source_action_item_id"], data["target_company"],
                data["start_url"], data["allowed_domain"],
                json.dumps(data["requested_topics"]), data["max_pages"],
                data["created_at"],
            ),
        )

    def get_browser_task(self, browser_task_id: UUID) -> BrowserTask | None:
        row = self.database.fetch_one(
            "SELECT * FROM browser_tasks WHERE browser_task_id = ?",
            (str(browser_task_id),),
        )
        if not row:
            return None
        row["requested_topics"] = json.loads(row["requested_topics"])
        return BrowserTask.model_validate(row)

    def save_execution_request(self, request: ExecutionRequest) -> None:
        data = _payload(request)
        self.database.execute(
            """
            INSERT INTO execution_requests (
                execution_request_id, browser_task_id, user_instruction,
                validation_status, rejection_reason, requested_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(execution_request_id) DO UPDATE SET
                browser_task_id=excluded.browser_task_id,
                user_instruction=excluded.user_instruction,
                validation_status=excluded.validation_status,
                rejection_reason=excluded.rejection_reason,
                requested_at=excluded.requested_at
            """,
            (
                data["execution_request_id"], data["browser_task_id"],
                data["user_instruction"], data["validation_status"],
                data["rejection_reason"], data["requested_at"],
            ),
        )

    def get_execution_request(
        self, execution_request_id: UUID
    ) -> ExecutionRequest | None:
        row = self.database.fetch_one(
            """
            SELECT * FROM execution_requests WHERE execution_request_id = ?
            """,
            (str(execution_request_id),),
        )
        return ExecutionRequest.model_validate(row) if row else None

    def save_browser_run(self, run: BrowserRun) -> None:
        data = _payload(run)
        self.database.execute(
            """
            INSERT INTO browser_runs (
                run_id, execution_request_id, status, visited_page_count,
                error_summary, started_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                execution_request_id=excluded.execution_request_id,
                status=excluded.status,
                visited_page_count=excluded.visited_page_count,
                error_summary=excluded.error_summary,
                started_at=excluded.started_at,
                completed_at=excluded.completed_at
            """,
            (
                data["run_id"], data["execution_request_id"], data["status"],
                data["visited_page_count"], data["error_summary"],
                data["started_at"], data["completed_at"],
            ),
        )

    def get_browser_run(self, run_id: UUID) -> BrowserRun | None:
        row = self.database.fetch_one(
            "SELECT * FROM browser_runs WHERE run_id = ?", (str(run_id),)
        )
        return BrowserRun.model_validate(row) if row else None

    def save_action_log(self, log: BrowserActionLog) -> None:
        data = _payload(log)
        self.database.execute(
            """
            INSERT INTO browser_action_logs (
                action_log_id, run_id, sequence, action_type, target_url,
                reason, status, detail, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(action_log_id) DO UPDATE SET
                run_id=excluded.run_id,
                sequence=excluded.sequence,
                action_type=excluded.action_type,
                target_url=excluded.target_url,
                reason=excluded.reason,
                status=excluded.status,
                detail=excluded.detail,
                occurred_at=excluded.occurred_at
            """,
            (
                data["action_log_id"], data["run_id"], data["sequence"],
                data["action_type"], data["target_url"], data["reason"],
                data["status"], data["detail"], data["occurred_at"],
            ),
        )

    def list_action_logs(self, run_id: UUID) -> list[BrowserActionLog]:
        rows = self.database.fetch_all(
            """
            SELECT * FROM browser_action_logs
            WHERE run_id = ? ORDER BY sequence
            """,
            (str(run_id),),
        )
        return [BrowserActionLog.model_validate(row) for row in rows]

    def save_research_finding(self, finding: ResearchFinding) -> None:
        data = _payload(finding)
        self.database.execute(
            """
            INSERT INTO research_findings (
                finding_id, run_id, topic, status, summary, source_urls, message
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(finding_id) DO UPDATE SET
                run_id=excluded.run_id,
                topic=excluded.topic,
                status=excluded.status,
                summary=excluded.summary,
                source_urls=excluded.source_urls,
                message=excluded.message
            """,
            (
                data["finding_id"], data["run_id"], data["topic"],
                data["status"], data["summary"],
                json.dumps(data["source_urls"]), data["message"],
            ),
        )

    def list_research_findings(self, run_id: UUID) -> list[ResearchFinding]:
        rows = self.database.fetch_all(
            """
            SELECT * FROM research_findings WHERE run_id = ? ORDER BY topic
            """,
            (str(run_id),),
        )
        for row in rows:
            row["source_urls"] = json.loads(row["source_urls"])
        return [ResearchFinding.model_validate(row) for row in rows]
