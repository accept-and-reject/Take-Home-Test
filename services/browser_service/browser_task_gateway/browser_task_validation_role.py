"""Validate Browser Task schema and permitted research scope."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from shared.contracts import BrowserTask, ResearchTopic
from shared.settings import BrowserSettings


@dataclass(frozen=True)
class TaskSchemaValidation:
    task: BrowserTask | None
    reasons: list[str]

    @property
    def accepted(self) -> bool:
        return self.task is not None


@dataclass(frozen=True)
class TaskScopeValidation:
    accepted: bool
    reasons: list[str]


def validate_task_schema(payload: object) -> TaskSchemaValidation:
    """Parse one BrowserTask without applying deployment-specific policy."""

    try:
        return TaskSchemaValidation(
            task=BrowserTask.model_validate(payload),
            reasons=[],
        )
    except ValidationError as exc:
        reasons = [
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors(include_url=False)
        ]
        return TaskSchemaValidation(task=None, reasons=reasons)


def validate_task_scope(
    task: BrowserTask,
    settings: BrowserSettings,
) -> TaskScopeValidation:
    """Restrict a valid task to the configured company and official site."""

    reasons: list[str] = []
    if task.target_company.casefold() != settings.company_name.casefold():
        reasons.append("target_company is outside the configured company scope")
    if _normalized_url(task.start_url) != _normalized_url(settings.start_url):
        reasons.append("start_url is not the configured official homepage")
    if _normalized_domain(task.allowed_domain) != _normalized_domain(
        settings.allowed_domain
    ):
        reasons.append("allowed_domain is outside the configured official domain")

    expected_topics = set(ResearchTopic)
    supplied_topics = set(task.requested_topics)
    if supplied_topics != expected_topics or len(task.requested_topics) != len(
        expected_topics
    ):
        reasons.append("requested_topics must contain each supported topic once")
    if task.max_pages > settings.max_pages:
        reasons.append(
            f"max_pages exceeds the configured limit of {settings.max_pages}"
        )
    return TaskScopeValidation(accepted=not reasons, reasons=reasons)


def _normalized_url(value: Any) -> str:
    return str(value).rstrip("/").casefold()


def _normalized_domain(value: str) -> str:
    return value.strip().rstrip(".").casefold()
