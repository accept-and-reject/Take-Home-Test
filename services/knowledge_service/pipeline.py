"""Orchestrate isolated, traceable meeting-document ingestion."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from services.knowledge_service.meeting_knowledge.action_item_extraction_role import (
    extract_action_items,
    merge_action_item_evidence,
    store_action_items,
)
from services.knowledge_service.meeting_knowledge.document_ingestion_role import (
    SUPPORTED_SOURCE_TYPES,
    read_meeting_document,
    register_source_document,
)
from services.knowledge_service.meeting_knowledge.knowledge_preparation_role import (
    build_traceable_chunks,
    clean_meeting_content,
    embed_chunks,
)
from services.knowledge_service.store import (
    KnowledgeApplicationDataStore,
    VectorKnowledgeStore,
)
from shared.contracts import (
    ActionItem,
    ContentChunk,
    ProcessingResult,
    ProcessingStatus,
    SourceDocument,
    SourceDocumentStatus,
)
from shared.openai_gateway import OpenAIGateway


@dataclass(frozen=True)
class DocumentIngestionSummary:
    source_id: str
    source_type: str
    title: str
    source_path: str
    status: str
    content_hash: str
    extracted_character_count: int
    cleaned_character_count: int
    chunk_count: int
    embedding_dimension: int | None
    locators: list[str]
    chunk_samples: list[dict[str, object]]
    warnings: list[str]
    error_message: str | None


@dataclass(frozen=True)
class BatchIngestionSummary:
    status: str
    input_path: str
    discovered_file_count: int
    processed_file_count: int
    skipped_file_count: int
    failed_file_count: int
    source_document_count: int
    content_chunk_count: int
    vector_chunk_count: int
    action_item_count: int
    action_item_warnings: list[str]
    action_items: list[dict[str, object]]
    documents: list[DocumentIngestionSummary]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class MeetingIngestionPipeline:
    def __init__(
        self,
        *,
        application_store: KnowledgeApplicationDataStore,
        vector_store: VectorKnowledgeStore,
        openai_gateway: OpenAIGateway,
        embedding_model: str,
        agent_model: str,
        company_name: str,
        allowed_domain: str,
    ) -> None:
        self.application_store = application_store
        self.vector_store = vector_store
        self.openai_gateway = openai_gateway
        self.embedding_model = embedding_model
        self.agent_model = agent_model
        self.company_name = company_name
        self.allowed_domain = allowed_domain

    def ingest(self, input_path: Path) -> BatchIngestionSummary:
        files = _discover_sources(input_path)
        summaries = [self._ingest_file_isolated(path) for path in files]
        action_items, action_warnings = self._build_action_items(summaries)
        if action_warnings:
            summaries = [
                replace(
                    summary,
                    status=(
                        ProcessingStatus.PARTIAL.value
                        if summary.status
                        not in {ProcessingStatus.FAILED.value, "skipped"}
                        else summary.status
                    ),
                    warnings=list(dict.fromkeys([*summary.warnings, *action_warnings])),
                )
                for summary in summaries
            ]
        self._update_processing_action_counts(
            summaries,
            action_items,
            action_warnings,
        )
        failed_count = sum(
            item.status == ProcessingStatus.FAILED.value for item in summaries
        )
        skipped_count = sum(item.status == "skipped" for item in summaries)
        partial_count = sum(
            item.status == ProcessingStatus.PARTIAL.value for item in summaries
        )
        processed_count = len(summaries) - failed_count - skipped_count

        if summaries and failed_count == len(summaries):
            status = ProcessingStatus.FAILED.value
        elif failed_count or partial_count or action_warnings:
            status = ProcessingStatus.PARTIAL.value
        else:
            status = ProcessingStatus.COMPLETED.value

        return BatchIngestionSummary(
            status=status,
            input_path=str(input_path),
            discovered_file_count=len(files),
            processed_file_count=processed_count,
            skipped_file_count=skipped_count,
            failed_file_count=failed_count,
            source_document_count=self.application_store.count_source_documents(),
            content_chunk_count=self.application_store.count_content_chunks(),
            vector_chunk_count=self.vector_store.count_chunks(),
            action_item_count=len(action_items),
            action_item_warnings=action_warnings,
            action_items=[
                _action_item_summary(item, self.application_store)
                for item in action_items
            ],
            documents=summaries,
        )

    def _build_action_items(
        self,
        summaries: list[DocumentIngestionSummary],
    ) -> tuple[list[ActionItem], list[str]]:
        chunks = _chunks_for_summaries(summaries, self.application_store)
        if not chunks:
            return [], []

        relevant_chunk_ids = {chunk.chunk_id for chunk in chunks}
        existing_items = [
            item
            for item in self.application_store.list_action_items()
            if relevant_chunk_ids.intersection(item.evidence_chunk_ids)
        ]
        has_newly_processed_source = any(
            summary.status
            in {
                ProcessingStatus.COMPLETED.value,
                ProcessingStatus.PARTIAL.value,
            }
            for summary in summaries
        )
        if existing_items and not has_newly_processed_source:
            return existing_items, []

        extraction = extract_action_items(
            chunks,
            self.openai_gateway,
            self.agent_model,
            company_name=self.company_name,
        )
        if not extraction.candidates:
            return existing_items, extraction.warnings

        merged = merge_action_item_evidence(
            extraction.candidates,
            chunks,
            existing_items,
            company_name=self.company_name,
            allowed_domain=self.allowed_domain,
        )
        warnings = list(extraction.warnings)
        try:
            store_action_items(merged, self.application_store)
        except Exception as exc:
            warnings.append(f"action-item storage failed: {exc}")
            return existing_items, warnings

        stored_items = [
            item
            for item in self.application_store.list_action_items()
            if relevant_chunk_ids.intersection(item.evidence_chunk_ids)
        ]
        return stored_items, warnings

    def _update_processing_action_counts(
        self,
        summaries: list[DocumentIngestionSummary],
        action_items: list[ActionItem],
        action_warnings: list[str],
    ) -> None:
        evidence_chunks = {
            chunk_id: self.application_store.get_content_chunk(chunk_id)
            for item in action_items
            for chunk_id in item.evidence_chunk_ids
        }
        for summary in summaries:
            if summary.source_id == "unregistered":
                continue
            source_id = UUID(summary.source_id)
            result = self.application_store.get_processing_result(source_id)
            if result is None:
                continue
            action_count = sum(
                any(
                    evidence_chunks.get(chunk_id) is not None
                    and evidence_chunks[chunk_id].source_id == source_id
                    for chunk_id in item.evidence_chunk_ids
                )
                for item in action_items
            )
            warnings = list(dict.fromkeys([*result.warnings, *action_warnings]))
            status = result.status
            if warnings and status is not ProcessingStatus.FAILED:
                status = ProcessingStatus.PARTIAL
            self.application_store.save_processing_result(
                result.model_copy(
                    update={
                        "status": status,
                        "action_item_count": action_count,
                        "warnings": warnings,
                    }
                )
            )

    def _ingest_file_isolated(self, path: Path) -> DocumentIngestionSummary:
        try:
            return self._ingest_file(path)
        except Exception as exc:
            return DocumentIngestionSummary(
                source_id="unregistered",
                source_type=path.suffix.lower().lstrip(".") or "unknown",
                title=path.stem,
                source_path=str(path),
                status=ProcessingStatus.FAILED.value,
                content_hash="unavailable",
                extracted_character_count=0,
                cleaned_character_count=0,
                chunk_count=0,
                embedding_dimension=None,
                locators=[],
                chunk_samples=[],
                warnings=[],
                error_message=str(exc) or exc.__class__.__name__,
            )

    def _ingest_file(self, path: Path) -> DocumentIngestionSummary:
        registration = register_source_document(path, self.application_store)
        document = registration.document
        if registration.duplicate:
            chunks = self.application_store.list_content_chunks(document.source_id)
            result = self.application_store.get_processing_result(document.source_id)
            return DocumentIngestionSummary(
                source_id=str(document.source_id),
                source_type=document.source_type.value,
                title=document.title,
                source_path=document.source_path,
                status="skipped",
                content_hash=document.content_hash,
                extracted_character_count=(
                    result.extracted_character_count if result else 0
                ),
                cleaned_character_count=(
                    result.cleaned_character_count if result else 0
                ),
                chunk_count=len(chunks),
                embedding_dimension=(len(chunks[0].embedding) if chunks else None),
                locators=_locator_summaries(chunks),
                chunk_samples=_chunk_samples(chunks),
                warnings=["duplicate content hash; existing source was reused"],
                error_message=document.error_message,
            )

        extracted_count = 0
        cleaned_count = 0
        warnings: list[str] = []
        try:
            extracted = read_meeting_document(path, document)
            extracted_count = extracted.character_count
            warnings.extend(extracted.warnings)

            cleaned = clean_meeting_content(extracted.blocks)
            cleaned_count = cleaned.character_count
            warnings.extend(cleaned.warnings)

            prepared = build_traceable_chunks(document.source_id, cleaned.blocks)
            chunks = embed_chunks(
                prepared,
                self.openai_gateway,
                self.embedding_model,
            )
            self.vector_store.initialize(len(chunks[0].embedding))
            self.vector_store.upsert_chunks(chunks)
            self.application_store.replace_content_chunks(document.source_id, chunks)

            processing_status = (
                ProcessingStatus.PARTIAL if warnings else ProcessingStatus.COMPLETED
            )
            document = _save_document_status(
                self.application_store,
                document,
                SourceDocumentStatus.PROCESSED,
            )
            self.application_store.save_processing_result(
                _processing_result(
                    document.source_id,
                    processing_status,
                    extracted_count,
                    cleaned_count,
                    len(chunks),
                    warnings,
                )
            )
            return DocumentIngestionSummary(
                source_id=str(document.source_id),
                source_type=document.source_type.value,
                title=document.title,
                source_path=document.source_path,
                status=processing_status.value,
                content_hash=document.content_hash,
                extracted_character_count=extracted_count,
                cleaned_character_count=cleaned_count,
                chunk_count=len(chunks),
                embedding_dimension=len(chunks[0].embedding),
                locators=_locator_summaries(chunks),
                chunk_samples=_chunk_samples(chunks),
                warnings=warnings,
                error_message=None,
            )
        except Exception as exc:
            error_message = str(exc) or exc.__class__.__name__
            document = _save_document_status(
                self.application_store,
                document,
                SourceDocumentStatus.FAILED,
                error_message,
            )
            self.application_store.save_processing_result(
                _processing_result(
                    document.source_id,
                    ProcessingStatus.FAILED,
                    extracted_count,
                    cleaned_count,
                    0,
                    warnings,
                    error_message,
                )
            )
            return DocumentIngestionSummary(
                source_id=str(document.source_id),
                source_type=document.source_type.value,
                title=document.title,
                source_path=document.source_path,
                status=ProcessingStatus.FAILED.value,
                content_hash=document.content_hash,
                extracted_character_count=extracted_count,
                cleaned_character_count=cleaned_count,
                chunk_count=0,
                embedding_dimension=None,
                locators=[],
                chunk_samples=[],
                warnings=warnings,
                error_message=error_message,
            )


def _discover_sources(input_path: Path) -> list[Path]:
    if input_path.is_file():
        candidates = [input_path]
    elif input_path.is_dir():
        candidates = [path for path in input_path.rglob("*") if path.is_file()]
    else:
        raise FileNotFoundError(f"input path does not exist: {input_path}")
    files = sorted(
        path for path in candidates if path.suffix.lower() in SUPPORTED_SOURCE_TYPES
    )
    if not files:
        raise ValueError(f"no supported PDF or PPTX files found in: {input_path}")
    return files


def _processing_result(
    source_id: UUID,
    status: ProcessingStatus,
    extracted_count: int,
    cleaned_count: int,
    chunk_count: int,
    warnings: list[str],
    error_message: str | None = None,
) -> ProcessingResult:
    return ProcessingResult(
        processing_id=uuid5(NAMESPACE_URL, f"meeting-processing:{source_id}"),
        source_id=source_id,
        status=status,
        extracted_character_count=extracted_count,
        cleaned_character_count=cleaned_count,
        chunk_count=chunk_count,
        action_item_count=0,
        warnings=warnings,
        error_message=error_message,
        processed_at=datetime.now(UTC),
    )


def _save_document_status(
    store: KnowledgeApplicationDataStore,
    document: SourceDocument,
    status: SourceDocumentStatus,
    error_message: str | None = None,
) -> SourceDocument:
    updated = document.model_copy(
        update={"status": status, "error_message": error_message}
    )
    store.save_source_document(updated)
    return updated


def _locator_summaries(chunks: list[ContentChunk]) -> list[str]:
    return list(
        dict.fromkeys(
            f"{chunk.locator_type.value}:{chunk.locator_value}" for chunk in chunks
        )
    )


def _chunk_samples(chunks: list[ContentChunk]) -> list[dict[str, object]]:
    return [
        {
            "sequence": chunk.sequence,
            "locator": f"{chunk.locator_type.value}:{chunk.locator_value}",
            "meeting_date": (
                chunk.meeting_date.isoformat() if chunk.meeting_date else None
            ),
            "project_hints": chunk.project_hints,
            "content_preview": chunk.content[:160],
            "embedding_model": chunk.embedding_model,
            "embedding_dimension": len(chunk.embedding),
        }
        for chunk in chunks[:3]
    ]


def _chunks_for_summaries(
    summaries: list[DocumentIngestionSummary],
    store: KnowledgeApplicationDataStore,
) -> list[ContentChunk]:
    chunks_by_id: dict[UUID, ContentChunk] = {}
    for summary in summaries:
        if summary.source_id == "unregistered":
            continue
        for chunk in store.list_content_chunks(UUID(summary.source_id)):
            chunks_by_id[chunk.chunk_id] = chunk
    return sorted(
        chunks_by_id.values(),
        key=lambda chunk: (
            chunk.meeting_date or datetime.min.date(),
            str(chunk.source_id),
            chunk.sequence,
        ),
    )


def _action_item_summary(
    item: ActionItem,
    store: KnowledgeApplicationDataStore,
) -> dict[str, object]:
    evidence = []
    for chunk_id in item.evidence_chunk_ids:
        chunk = store.get_content_chunk(chunk_id)
        if chunk is None:
            continue
        source = store.get_source_document(chunk.source_id)
        evidence.append(
            {
                "chunk_id": str(chunk.chunk_id),
                "source_title": source.title if source else "unknown",
                "meeting_date": (
                    chunk.meeting_date.isoformat() if chunk.meeting_date else None
                ),
                "locator": f"{chunk.locator_type.value}:{chunk.locator_value}",
                "excerpt": chunk.content[:160],
            }
        )
    return {
        "action_item_id": str(item.action_item_id),
        "project_name": item.project_name,
        "description": item.description,
        "status": item.status.value,
        "assignee": item.assignee,
        "due_date": item.due_date.isoformat() if item.due_date else None,
        "action_kind": item.action_kind.value,
        "target_company": item.target_company,
        "allowed_domain": item.allowed_domain,
        "requested_topics": [topic.value for topic in item.requested_topics],
        "latest_update_at": (
            item.latest_update_at.isoformat() if item.latest_update_at else None
        ),
        "evidence_chunk_ids": [
            str(chunk_id) for chunk_id in item.evidence_chunk_ids
        ],
        "evidence": evidence,
    }
