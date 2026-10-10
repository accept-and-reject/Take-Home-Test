"""Orchestrate isolated, traceable meeting-document ingestion."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

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
    ) -> None:
        self.application_store = application_store
        self.vector_store = vector_store
        self.openai_gateway = openai_gateway
        self.embedding_model = embedding_model

    def ingest(self, input_path: Path) -> BatchIngestionSummary:
        files = _discover_sources(input_path)
        summaries = [self._ingest_file_isolated(path) for path in files]
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
        elif failed_count or partial_count:
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
            documents=summaries,
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
