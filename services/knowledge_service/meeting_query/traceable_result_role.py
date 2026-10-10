"""Build persisted, traceable search results without inventing evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from uuid import NAMESPACE_URL, UUID, uuid5

from services.knowledge_service.meeting_query.meeting_search_role import (
    ActionItemMatch,
    KnowledgeChunkMatch,
)
from services.knowledge_service.store import KnowledgeApplicationDataStore
from shared.contracts import ActionItem, ContentChunk, SearchRequest, SearchResultItem


DEFAULT_MINIMUM_SCORE = 0.45


@dataclass(frozen=True)
class TraceableSearchResponse:
    status: str
    request_id: str
    result_count: int
    action_item_count: int
    minimum_score: float
    maximum_score: float | None
    results: list[dict[str, object]]
    action_items: list[dict[str, object]]
    warnings: list[str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def build_traceable_results(
    request: SearchRequest,
    chunk_matches: list[KnowledgeChunkMatch],
    action_matches: list[ActionItemMatch],
    store: KnowledgeApplicationDataStore,
    *,
    minimum_score: float = DEFAULT_MINIMUM_SCORE,
) -> TraceableSearchResponse:
    maximum_score = max((match.score for match in chunk_matches), default=None)
    accepted = [match for match in chunk_matches if match.score >= minimum_score]
    if not accepted:
        return mark_insufficient_evidence(
            request,
            minimum_score=minimum_score,
            maximum_score=maximum_score,
        )

    accepted_chunk_ids = {match.chunk.chunk_id for match in accepted}
    relevant_actions = [
        match
        for match in action_matches
        if accepted_chunk_ids.intersection(match.item.evidence_chunk_ids)
    ]
    action_ids_by_chunk: dict[UUID, list[UUID]] = {}
    for match in relevant_actions:
        for chunk_id in match.item.evidence_chunk_ids:
            action_ids_by_chunk.setdefault(chunk_id, []).append(
                match.item.action_item_id
            )

    persisted: list[SearchResultItem] = []
    warnings: list[str] = []
    for match in accepted:
        source = store.get_source_document(match.chunk.source_id)
        if source is None:
            warnings.append(
                f"excluded chunk {match.chunk.chunk_id}: source document missing"
            )
            continue
        action_ids = sorted(
            action_ids_by_chunk.get(match.chunk.chunk_id, []), key=str
        )
        result = SearchResultItem(
            result_item_id=uuid5(
                NAMESPACE_URL,
                f"meeting-search-result:{request.request_id}:{match.chunk.chunk_id}",
            ),
            request_id=request.request_id,
            chunk_id=match.chunk.chunk_id,
            action_item_id=action_ids[0] if action_ids else None,
            rank=len(persisted) + 1,
            score=match.score,
            excerpt=match.chunk.content,
            source_title=source.title,
            locator_type=match.chunk.locator_type,
            locator_value=match.chunk.locator_value,
        )
        store.save_search_result_item(result)
        persisted.append(result)

    if not persisted:
        return mark_insufficient_evidence(
            request,
            minimum_score=minimum_score,
            maximum_score=maximum_score,
            warnings=warnings,
        )

    return TraceableSearchResponse(
        status="completed",
        request_id=str(request.request_id),
        result_count=len(persisted),
        action_item_count=len(relevant_actions),
        minimum_score=minimum_score,
        maximum_score=maximum_score,
        results=[item.model_dump(mode="json") for item in persisted],
        action_items=[
            _action_item_summary(match.item, match.evidence_chunks, store)
            for match in relevant_actions
        ],
        warnings=warnings,
    )


def mark_insufficient_evidence(
    request: SearchRequest,
    *,
    minimum_score: float,
    maximum_score: float | None,
    warnings: list[str] | None = None,
) -> TraceableSearchResponse:
    return TraceableSearchResponse(
        status="insufficient_evidence",
        request_id=str(request.request_id),
        result_count=0,
        action_item_count=0,
        minimum_score=minimum_score,
        maximum_score=maximum_score,
        results=[],
        action_items=[],
        warnings=warnings or [],
    )


def _action_item_summary(
    item: ActionItem,
    evidence_chunks: list[ContentChunk],
    store: KnowledgeApplicationDataStore,
) -> dict[str, object]:
    evidence = []
    for chunk in evidence_chunks:
        source = store.get_source_document(chunk.source_id)
        if source is None:
            continue
        evidence.append(
            {
                "chunk_id": str(chunk.chunk_id),
                "source_title": source.title,
                "meeting_date": (
                    chunk.meeting_date.isoformat() if chunk.meeting_date else None
                ),
                "locator_type": chunk.locator_type.value,
                "locator_value": chunk.locator_value,
            }
        )
    return {
        **item.model_dump(mode="json"),
        "evidence": evidence,
    }
