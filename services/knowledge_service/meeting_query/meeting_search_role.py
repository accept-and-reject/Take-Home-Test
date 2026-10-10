"""Parse search scope and retrieve meeting chunks and action items."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import uuid4

from services.knowledge_service.store import (
    KnowledgeApplicationDataStore,
    VectorKnowledgeStore,
)
from shared.contracts import ActionItem, ActionStatus, ContentChunk, SearchRequest
from shared.openai_gateway import OpenAIGateway


@dataclass(frozen=True)
class KnowledgeChunkMatch:
    chunk: ContentChunk
    score: float


@dataclass(frozen=True)
class ActionItemMatch:
    item: ActionItem
    evidence_chunks: list[ContentChunk]


def parse_search_scope(
    query: str,
    store: KnowledgeApplicationDataStore,
    *,
    project_hint: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    action_status_filter: ActionStatus | None = None,
    top_k: int = 8,
) -> SearchRequest:
    request = SearchRequest(
        request_id=uuid4(),
        query=query,
        project_hint=project_hint,
        date_from=date_from,
        date_to=date_to,
        action_status_filter=action_status_filter,
        top_k=top_k,
        created_at=datetime.now(UTC),
    )
    store.save_search_request(request)
    return request


def search_knowledge_chunks(
    request: SearchRequest,
    application_store: KnowledgeApplicationDataStore,
    vector_store: VectorKnowledgeStore,
    gateway: OpenAIGateway,
    embedding_model: str,
) -> list[KnowledgeChunkMatch]:
    response = gateway.create_embeddings(model=embedding_model, input=request.query)
    embedding = list(response.data[0].embedding)
    if not embedding:
        raise ValueError("query embedding response was empty")

    # The fixture contains only a few chunks. Retrieve the complete ranked set,
    # then apply normalized metadata filters against the validated application
    # records so older indexed payload formats remain searchable.
    candidate_limit = max(request.top_k, vector_store.count_chunks())
    hits = vector_store.search_chunks(embedding, limit=candidate_limit)

    matches: list[KnowledgeChunkMatch] = []
    for hit in hits:
        chunk = application_store.get_content_chunk(hit.chunk_id)
        if chunk is None or not _chunk_matches_scope(chunk, request):
            continue
        matches.append(KnowledgeChunkMatch(chunk=chunk, score=hit.score))
        if len(matches) == request.top_k:
            break
    return matches


def search_action_items(
    request: SearchRequest,
    chunk_matches: list[KnowledgeChunkMatch],
    store: KnowledgeApplicationDataStore,
) -> list[ActionItemMatch]:
    matched_chunk_ids = {match.chunk.chunk_id for match in chunk_matches}
    results: list[ActionItemMatch] = []
    for item in store.list_action_items():
        if not _action_matches_scope(item, request):
            continue
        if not matched_chunk_ids.intersection(item.evidence_chunk_ids):
            continue
        evidence = [
            chunk
            for chunk_id in item.evidence_chunk_ids
            if (chunk := store.get_content_chunk(chunk_id)) is not None
            and _chunk_matches_date(chunk, request)
        ]
        if evidence:
            results.append(
                ActionItemMatch(
                    item=item,
                    evidence_chunks=sorted(
                        evidence,
                        key=lambda chunk: (
                            chunk.meeting_date or date.min,
                            str(chunk.source_id),
                            chunk.sequence,
                        ),
                    ),
                )
            )
    return sorted(results, key=lambda match: str(match.item.action_item_id))


def _chunk_matches_scope(chunk: ContentChunk, request: SearchRequest) -> bool:
    if request.project_hint:
        expected = request.project_hint.casefold()
        if not any(expected == hint.casefold() for hint in chunk.project_hints):
            return False
    return _chunk_matches_date(chunk, request)


def _chunk_matches_date(chunk: ContentChunk, request: SearchRequest) -> bool:
    if request.date_from and (
        chunk.meeting_date is None or chunk.meeting_date < request.date_from
    ):
        return False
    if request.date_to and (
        chunk.meeting_date is None or chunk.meeting_date > request.date_to
    ):
        return False
    return True


def _action_matches_scope(item: ActionItem, request: SearchRequest) -> bool:
    if request.project_hint and (
        request.project_hint.casefold() != item.project_name.casefold()
    ):
        return False
    return not (
        request.action_status_filter
        and item.status is not request.action_status_filter
    )
