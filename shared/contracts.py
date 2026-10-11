"""Validated data contracts shared across service boundaries.

The models mirror ``doc/02_系統設計/02_Data-Schema.md``.  They contain
data-integrity rules only; workflow authorization stays in the owning service.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


NonEmptyText = Annotated[str, Field(min_length=1)]
NonNegativeInt = Annotated[int, Field(ge=0)]
PositiveInt = Annotated[int, Field(ge=1)]


class SourceType(StrEnum):
    PDF = "pdf"
    PPTX = "pptx"


class SourceDocumentStatus(StrEnum):
    REGISTERED = "registered"
    PROCESSED = "processed"
    FAILED = "failed"


class ProcessingStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class LocatorType(StrEnum):
    PDF_PAGE = "pdf_page"
    PPTX_SLIDE = "pptx_slide"


class ActionStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class ActionKind(StrEnum):
    COMPANY_RESEARCH = "company_research"
    OTHER = "other"


class ResearchTopic(StrEnum):
    COMPANY_OVERVIEW = "company_overview"
    PRODUCTS = "products"
    BENEFITS = "benefits"


class BrowserTaskSource(StrEnum):
    ACTION_ITEM = "action_item"
    DIRECT = "direct"


class ExecutionValidationStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class BrowserRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class BrowserActionType(StrEnum):
    OPEN = "open"
    INSPECT = "inspect"
    FOLLOW_LINK = "follow_link"
    CAPTURE = "capture"
    SKIP = "skip"
    ERROR = "error"
    FINISH = "finish"


class BrowserActionStatus(StrEnum):
    SUCCESS = "success"
    SKIPPED = "skipped"
    FAILED = "failed"


class FindingStatus(StrEnum):
    FOUND = "found"
    NOT_FOUND = "not_found"
    FAILED = "failed"


class ContractModel(BaseModel):
    """Base behavior for every persisted or exchanged contract."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SourceDocument(ContractModel):
    source_id: UUID
    source_type: SourceType
    title: NonEmptyText
    source_path: NonEmptyText
    content_hash: NonEmptyText
    status: SourceDocumentStatus
    error_message: str | None = None


class ProcessingResult(ContractModel):
    processing_id: UUID
    source_id: UUID
    status: ProcessingStatus
    extracted_character_count: NonNegativeInt
    cleaned_character_count: NonNegativeInt
    chunk_count: NonNegativeInt
    action_item_count: NonNegativeInt
    warnings: list[str]
    error_message: str | None = None
    processed_at: datetime


class ContentChunk(ContractModel):
    chunk_id: UUID
    source_id: UUID
    sequence: PositiveInt
    content: NonEmptyText
    meeting_date: date | None = None
    project_hints: list[str]
    locator_type: LocatorType
    locator_value: NonEmptyText
    embedding: list[float]
    embedding_model: NonEmptyText
    content_hash: NonEmptyText


class ActionItem(ContractModel):
    action_item_id: UUID
    project_name: NonEmptyText
    description: NonEmptyText
    status: ActionStatus
    assignee: str | None = None
    due_date: date | None = None
    action_kind: ActionKind
    target_company: str | None = None
    allowed_domain: str | None = None
    requested_topics: list[ResearchTopic]
    latest_update_at: date | None = None
    evidence_chunk_ids: list[UUID]

    @model_validator(mode="after")
    def validate_research_fields(self) -> "ActionItem":
        if self.action_kind is ActionKind.COMPANY_RESEARCH:
            missing = [
                name
                for name, value in (
                    ("target_company", self.target_company),
                    ("allowed_domain", self.allowed_domain),
                    ("requested_topics", self.requested_topics),
                )
                if not value
            ]
            if missing:
                raise ValueError(
                    "company_research requires " + ", ".join(missing)
                )
        elif self.requested_topics:
            raise ValueError("other action items must not request research topics")
        return self


class BrowserTask(ContractModel):
    browser_task_id: UUID
    task_source: BrowserTaskSource
    source_action_item_id: UUID | None = None
    target_company: NonEmptyText
    start_url: HttpUrl
    allowed_domain: NonEmptyText
    requested_topics: Annotated[list[ResearchTopic], Field(min_length=1)]
    max_pages: Annotated[int, Field(ge=1, le=5)]
    created_at: datetime

    @model_validator(mode="after")
    def validate_task_source(self) -> "BrowserTask":
        has_action_item = self.source_action_item_id is not None
        if self.task_source is BrowserTaskSource.ACTION_ITEM and not has_action_item:
            raise ValueError("action_item tasks require source_action_item_id")
        if self.task_source is BrowserTaskSource.DIRECT and has_action_item:
            raise ValueError("direct tasks must not include source_action_item_id")
        return self


class SearchRequest(ContractModel):
    request_id: UUID
    query: NonEmptyText
    project_hint: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    action_status_filter: ActionStatus | None = None
    top_k: Annotated[int, Field(ge=1, le=50)]
    created_at: datetime

    @model_validator(mode="after")
    def validate_date_range(self) -> "SearchRequest":
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must not be after date_to")
        return self


class SearchResultItem(ContractModel):
    result_item_id: UUID
    request_id: UUID
    chunk_id: UUID
    action_item_id: UUID | None = None
    rank: PositiveInt
    score: float
    excerpt: NonEmptyText
    source_title: NonEmptyText
    locator_type: LocatorType
    locator_value: NonEmptyText


class ExecutionRequest(ContractModel):
    execution_request_id: UUID
    browser_task_id: UUID
    user_instruction: NonEmptyText
    validation_status: ExecutionValidationStatus
    rejection_reason: str | None = None
    requested_at: datetime

    @model_validator(mode="after")
    def validate_rejection(self) -> "ExecutionRequest":
        if (
            self.validation_status is ExecutionValidationStatus.REJECTED
            and not self.rejection_reason
        ):
            raise ValueError("rejected requests require rejection_reason")
        return self


class BrowserRun(ContractModel):
    run_id: UUID
    execution_request_id: UUID
    status: BrowserRunStatus
    visited_page_count: NonNegativeInt
    error_summary: str | None = None
    started_at: datetime
    completed_at: datetime | None = None


class BrowserActionLog(ContractModel):
    action_log_id: UUID
    run_id: UUID
    sequence: PositiveInt
    action_type: BrowserActionType
    target_url: HttpUrl | None = None
    reason: str | None = None
    status: BrowserActionStatus
    detail: str | None = None
    occurred_at: datetime


class ResearchFinding(ContractModel):
    finding_id: UUID
    run_id: UUID
    topic: ResearchTopic
    status: FindingStatus
    summary: str | None = None
    evidence_quote: str | None = None
    evidence_reason: str | None = None
    source_urls: list[HttpUrl]
    message: str | None = None

    @model_validator(mode="after")
    def validate_finding_payload(self) -> "ResearchFinding":
        if self.status is FindingStatus.FOUND:
            if (
                not self.summary
                or not self.evidence_quote
                or not self.evidence_reason
                or not self.source_urls
            ):
                raise ValueError(
                    "found findings require summary, evidence, and source_urls"
                )
        elif self.source_urls or self.evidence_quote or self.evidence_reason:
            raise ValueError(
                "not_found or failed findings must not include evidence or source_urls"
            )
        return self
