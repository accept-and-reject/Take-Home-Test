"""Expose read-only, traceable Knowledge operations as MCP tools."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from mcp.server import MCPServer

from services.knowledge_service.knowledge_mcp.integrated_execution_role import (
    ActionItemExecutionRejected,
    build_task_from_action_item,
    call_browser_executor,
)
from services.knowledge_service.meeting_query.meeting_search_role import (
    parse_search_scope,
    search_action_items,
    search_knowledge_chunks,
)
from services.knowledge_service.meeting_query.traceable_result_role import (
    build_traceable_results,
)
from services.knowledge_service.store import (
    KnowledgeApplicationDataStore,
    VectorKnowledgeStore,
)
from shared.contracts import ActionItem, ActionStatus, ContentChunk
from shared.openai_gateway import OpenAIGateway
from shared.settings import KnowledgeSettings


def create_knowledge_mcp_server(
    settings: KnowledgeSettings,
    store: KnowledgeApplicationDataStore,
) -> MCPServer:
    server = MCPServer(
        "meeting-action-research-assistant",
        instructions=(
            "Search only the indexed meeting knowledge. Present source titles and "
            "locators with factual claims. If status is insufficient_evidence, "
            "state that the documents do not contain enough evidence. Never infer "
            "missing facts. Use execute_action_item only after the user explicitly "
            "asks to execute a selected ActionItem; never imply that a Browser Task "
            "was executed unless that tool returns a Browser Run result."
        ),
    )

    @server.tool()
    def search_meetings(
        query: str,
        project_hint: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        action_status_filter: ActionStatus | None = None,
        top_k: int = 8,
    ) -> dict[str, object]:
        """Search meeting evidence and action items.

        The natural-language query is sufficient by itself. Supply optional
        project, date, status, or result-count filters only when the user's
        request makes that scope explicit. This tool never starts a browser.
        """

        request = parse_search_scope(
            query,
            store,
            project_hint=project_hint,
            date_from=date_from,
            date_to=date_to,
            action_status_filter=action_status_filter,
            top_k=top_k,
        )
        vector_store = VectorKnowledgeStore(settings.data_dir)
        try:
            chunks = search_knowledge_chunks(
                request,
                store,
                vector_store,
                OpenAIGateway(settings.openai_api_key),
                settings.embedding_model,
            )
            actions = search_action_items(request, chunks, store)
            return build_traceable_results(
                request,
                chunks,
                actions,
                store,
            ).to_dict()
        finally:
            vector_store.close()

    @server.tool()
    def get_action_item(action_item_id: UUID) -> dict[str, object]:
        """Return one ActionItem and all traceable meeting evidence by stable ID.

        This read-only tool does not execute the action item or contact the
        Browser service.
        """

        item = store.get_action_item(action_item_id)
        if item is None:
            return {
                "status": "not_found",
                "action_item_id": str(action_item_id),
            }
        return {
            "status": "found",
            "action_item": _action_item_summary(item, store),
        }

    @server.tool()
    def execute_action_item(action_item_id: UUID) -> dict[str, object]:
        """Execute one explicitly selected company-research ActionItem.

        This tool starts a bounded Browser workflow and can consume external API
        quota. Call it only after the user explicitly asks to execute the selected
        ActionItem. Use get_action_item for read-only inspection.
        """

        try:
            context = build_task_from_action_item(
                action_item_id,
                settings,
                store,
            )
        except ActionItemExecutionRejected as exc:
            return {
                "status": "rejected",
                "action_item_id": str(action_item_id),
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                },
            }
        result = call_browser_executor(context, settings)
        result["action_item"] = _action_item_summary(
            context.action_item,
            store,
        )
        return result

    return server


def _action_item_summary(
    item: ActionItem,
    store: KnowledgeApplicationDataStore,
) -> dict[str, object]:
    evidence: list[dict[str, object]] = []
    chunks: list[ContentChunk] = []
    for chunk_id in item.evidence_chunk_ids:
        chunk = store.get_content_chunk(chunk_id)
        if chunk is not None:
            chunks.append(chunk)
    for chunk in sorted(
        chunks,
        key=lambda value: (
            value.meeting_date or date.min,
            str(value.source_id),
            value.sequence,
        ),
    ):
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
                "excerpt": chunk.content,
            }
        )
    return {
        **item.model_dump(mode="json"),
        "evidence": evidence,
    }
