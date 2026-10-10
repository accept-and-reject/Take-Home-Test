"""Run a direct, traceable Knowledge search before MCP transport is added."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

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
from shared.contracts import ActionStatus
from shared.openai_gateway import OpenAIGateway
from shared.settings import KnowledgeSettings


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Search indexed meeting chunks and action items."
    )
    parser.add_argument("--query", required=True)
    parser.add_argument("--project-hint")
    parser.add_argument("--date-from", type=_date)
    parser.add_argument("--date-to", type=_date)
    parser.add_argument(
        "--action-status",
        type=ActionStatus,
        choices=list(ActionStatus),
    )
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--data-dir", type=Path)
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    settings = KnowledgeSettings.from_env()
    data_dir = arguments.data_dir or settings.data_dir
    application_store = KnowledgeApplicationDataStore(data_dir)
    vector_store = VectorKnowledgeStore(data_dir)
    application_store.initialize()

    try:
        request = parse_search_scope(
            arguments.query,
            application_store,
            project_hint=arguments.project_hint,
            date_from=arguments.date_from,
            date_to=arguments.date_to,
            action_status_filter=arguments.action_status,
            top_k=arguments.top_k,
        )
        chunk_matches = search_knowledge_chunks(
            request,
            application_store,
            vector_store,
            OpenAIGateway(settings.openai_api_key),
            settings.embedding_model,
        )
        action_matches = search_action_items(
            request,
            chunk_matches,
            application_store,
        )
        result = build_traceable_results(
            request,
            chunk_matches,
            action_matches,
            application_store,
        )
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0
    finally:
        vector_store.close()


if __name__ == "__main__":
    raise SystemExit(main())
