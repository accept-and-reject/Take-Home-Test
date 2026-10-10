"""Import PDF/PPTX meeting documents into the local hybrid knowledge base."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from services.knowledge_service.pipeline import MeetingIngestionPipeline
from services.knowledge_service.store import (
    KnowledgeApplicationDataStore,
    VectorKnowledgeStore,
)
from shared.openai_gateway import OpenAIGateway
from shared.settings import KnowledgeSettings


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract, clean, chunk, embed, and index meeting documents."
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="PDF/PPTX file or directory to ingest",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        help="Override KNOWLEDGE_DATA_DIR for an isolated verification run",
    )
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    settings = KnowledgeSettings.from_env()
    data_dir = arguments.data_dir or settings.data_dir
    application_store = KnowledgeApplicationDataStore(data_dir)
    vector_store = VectorKnowledgeStore(data_dir)
    application_store.initialize()

    try:
        pipeline = MeetingIngestionPipeline(
            application_store=application_store,
            vector_store=vector_store,
            openai_gateway=OpenAIGateway(settings.openai_api_key),
            embedding_model=settings.embedding_model,
        )
        summary = pipeline.ingest(arguments.input)
        print(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2))
        return 1 if summary.status == "failed" else 0
    finally:
        vector_store.close()


if __name__ == "__main__":
    raise SystemExit(main())
