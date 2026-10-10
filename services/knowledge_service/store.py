"""SQLite persistence owned exclusively by the Knowledge service."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence, TypeVar
from uuid import UUID

from pydantic import BaseModel
from qdrant_client import QdrantClient, models

from shared.contracts import (
    ActionItem,
    ContentChunk,
    ProcessingResult,
    SearchRequest,
    SearchResultItem,
    SourceDocument,
)
from shared.sqlite_store import SQLiteStore


ModelT = TypeVar("ModelT", bound=BaseModel)


KNOWLEDGE_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS source_documents (
        source_id TEXT PRIMARY KEY,
        source_type TEXT NOT NULL,
        title TEXT NOT NULL,
        source_path TEXT NOT NULL,
        content_hash TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL,
        error_message TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS processing_results (
        processing_id TEXT PRIMARY KEY,
        source_id TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL,
        extracted_character_count INTEGER NOT NULL CHECK (extracted_character_count >= 0),
        cleaned_character_count INTEGER NOT NULL CHECK (cleaned_character_count >= 0),
        chunk_count INTEGER NOT NULL CHECK (chunk_count >= 0),
        action_item_count INTEGER NOT NULL CHECK (action_item_count >= 0),
        warnings TEXT NOT NULL,
        error_message TEXT,
        processed_at TEXT NOT NULL,
        FOREIGN KEY (source_id) REFERENCES source_documents(source_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS content_chunks (
        chunk_id TEXT PRIMARY KEY,
        source_id TEXT NOT NULL,
        sequence INTEGER NOT NULL CHECK (sequence >= 1),
        content TEXT NOT NULL,
        meeting_date TEXT,
        project_hints TEXT NOT NULL,
        locator_type TEXT NOT NULL,
        locator_value TEXT NOT NULL,
        embedding TEXT NOT NULL,
        embedding_model TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        FOREIGN KEY (source_id) REFERENCES source_documents(source_id),
        UNIQUE (source_id, sequence),
        UNIQUE (source_id, content_hash)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS action_items (
        action_item_id TEXT PRIMARY KEY,
        project_name TEXT NOT NULL,
        description TEXT NOT NULL,
        status TEXT NOT NULL,
        assignee TEXT,
        due_date TEXT,
        action_kind TEXT NOT NULL,
        target_company TEXT,
        allowed_domain TEXT,
        requested_topics TEXT NOT NULL,
        latest_update_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS action_item_evidence (
        action_item_id TEXT NOT NULL,
        chunk_id TEXT NOT NULL,
        PRIMARY KEY (action_item_id, chunk_id),
        FOREIGN KEY (action_item_id) REFERENCES action_items(action_item_id) ON DELETE CASCADE,
        FOREIGN KEY (chunk_id) REFERENCES content_chunks(chunk_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS search_requests (
        request_id TEXT PRIMARY KEY,
        query TEXT NOT NULL,
        project_hint TEXT,
        date_from TEXT,
        date_to TEXT,
        action_status_filter TEXT,
        top_k INTEGER NOT NULL CHECK (top_k >= 1),
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS search_result_items (
        result_item_id TEXT PRIMARY KEY,
        request_id TEXT NOT NULL,
        chunk_id TEXT NOT NULL,
        action_item_id TEXT,
        rank INTEGER NOT NULL CHECK (rank >= 1),
        score REAL NOT NULL,
        excerpt TEXT NOT NULL,
        source_title TEXT NOT NULL,
        locator_type TEXT NOT NULL,
        locator_value TEXT NOT NULL,
        FOREIGN KEY (request_id) REFERENCES search_requests(request_id) ON DELETE CASCADE,
        FOREIGN KEY (chunk_id) REFERENCES content_chunks(chunk_id),
        FOREIGN KEY (action_item_id) REFERENCES action_items(action_item_id),
        UNIQUE (request_id, rank)
    )
    """,
)


def _payload(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")


def _decode_json_columns(
    row: dict[str, Any], *columns: str
) -> dict[str, Any]:
    for column in columns:
        row[column] = json.loads(row[column])
    return row


class KnowledgeApplicationDataStore:
    """Persist Knowledge entities without exposing the database to Browser."""

    def __init__(self, data_dir: Path) -> None:
        self.database = SQLiteStore(data_dir / "knowledge.sqlite3")

    @property
    def database_path(self) -> Path:
        return self.database.database_path

    def initialize(self) -> None:
        self.database.initialize(KNOWLEDGE_SCHEMA)

    def is_healthy(self) -> bool:
        return self.database.is_healthy()

    def save_source_document(self, document: SourceDocument) -> None:
        data = _payload(document)
        self.database.execute(
            """
            INSERT INTO source_documents (
                source_id, source_type, title, source_path, content_hash,
                status, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
                source_type=excluded.source_type,
                title=excluded.title,
                source_path=excluded.source_path,
                content_hash=excluded.content_hash,
                status=excluded.status,
                error_message=excluded.error_message
            """,
            (
                data["source_id"], data["source_type"], data["title"],
                data["source_path"], data["content_hash"], data["status"],
                data["error_message"],
            ),
        )

    def get_source_document(self, source_id: UUID) -> SourceDocument | None:
        row = self.database.fetch_one(
            "SELECT * FROM source_documents WHERE source_id = ?", (str(source_id),)
        )
        return SourceDocument.model_validate(row) if row else None

    def get_source_document_by_hash(
        self, content_hash: str
    ) -> SourceDocument | None:
        row = self.database.fetch_one(
            "SELECT * FROM source_documents WHERE content_hash = ?", (content_hash,)
        )
        return SourceDocument.model_validate(row) if row else None

    def save_processing_result(self, result: ProcessingResult) -> None:
        data = _payload(result)
        self.database.execute(
            """
            INSERT INTO processing_results (
                processing_id, source_id, status, extracted_character_count,
                cleaned_character_count, chunk_count, action_item_count,
                warnings, error_message, processed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
                processing_id=excluded.processing_id,
                status=excluded.status,
                extracted_character_count=excluded.extracted_character_count,
                cleaned_character_count=excluded.cleaned_character_count,
                chunk_count=excluded.chunk_count,
                action_item_count=excluded.action_item_count,
                warnings=excluded.warnings,
                error_message=excluded.error_message,
                processed_at=excluded.processed_at
            """,
            (
                data["processing_id"], data["source_id"], data["status"],
                data["extracted_character_count"],
                data["cleaned_character_count"], data["chunk_count"],
                data["action_item_count"], json.dumps(data["warnings"]),
                data["error_message"], data["processed_at"],
            ),
        )

    def get_processing_result(self, source_id: UUID) -> ProcessingResult | None:
        row = self.database.fetch_one(
            "SELECT * FROM processing_results WHERE source_id = ?", (str(source_id),)
        )
        if not row:
            return None
        return ProcessingResult.model_validate(_decode_json_columns(row, "warnings"))

    def save_content_chunk(self, chunk: ContentChunk) -> None:
        data = _payload(chunk)
        self.database.execute(
            """
            INSERT INTO content_chunks (
                chunk_id, source_id, sequence, content, meeting_date,
                project_hints, locator_type, locator_value, embedding,
                embedding_model, content_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chunk_id) DO UPDATE SET
                source_id=excluded.source_id,
                sequence=excluded.sequence,
                content=excluded.content,
                meeting_date=excluded.meeting_date,
                project_hints=excluded.project_hints,
                locator_type=excluded.locator_type,
                locator_value=excluded.locator_value,
                embedding=excluded.embedding,
                embedding_model=excluded.embedding_model,
                content_hash=excluded.content_hash
            """,
            (
                data["chunk_id"], data["source_id"], data["sequence"],
                data["content"], data["meeting_date"],
                json.dumps(data["project_hints"], ensure_ascii=False),
                data["locator_type"], data["locator_value"],
                json.dumps(data["embedding"]), data["embedding_model"],
                data["content_hash"],
            ),
        )

    def get_content_chunk(self, chunk_id: UUID) -> ContentChunk | None:
        row = self.database.fetch_one(
            "SELECT * FROM content_chunks WHERE chunk_id = ?", (str(chunk_id),)
        )
        if not row:
            return None
        return ContentChunk.model_validate(
            _decode_json_columns(row, "project_hints", "embedding")
        )

    def replace_content_chunks(
        self, source_id: UUID, chunks: Sequence[ContentChunk]
    ) -> None:
        """Atomically replace every application-store chunk for one source."""

        with self.database.connect() as connection:
            connection.execute(
                "DELETE FROM content_chunks WHERE source_id = ?", (str(source_id),)
            )
            connection.executemany(
                """
                INSERT INTO content_chunks (
                    chunk_id, source_id, sequence, content, meeting_date,
                    project_hints, locator_type, locator_value, embedding,
                    embedding_model, content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(chunk.chunk_id), str(chunk.source_id), chunk.sequence,
                        chunk.content,
                        chunk.meeting_date.isoformat()
                        if chunk.meeting_date else None,
                        json.dumps(chunk.project_hints, ensure_ascii=False),
                        chunk.locator_type.value, chunk.locator_value,
                        json.dumps(chunk.embedding), chunk.embedding_model,
                        chunk.content_hash,
                    )
                    for chunk in chunks
                ],
            )

    def list_content_chunks(self, source_id: UUID) -> list[ContentChunk]:
        rows = self.database.fetch_all(
            """
            SELECT * FROM content_chunks
            WHERE source_id = ? ORDER BY sequence
            """,
            (str(source_id),),
        )
        return [
            ContentChunk.model_validate(
                _decode_json_columns(row, "project_hints", "embedding")
            )
            for row in rows
        ]

    def count_source_documents(self) -> int:
        row = self.database.fetch_one("SELECT COUNT(*) AS count FROM source_documents")
        return int(row["count"]) if row else 0

    def count_content_chunks(self) -> int:
        row = self.database.fetch_one("SELECT COUNT(*) AS count FROM content_chunks")
        return int(row["count"]) if row else 0

    def save_action_item(self, item: ActionItem) -> None:
        data = _payload(item)
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO action_items (
                    action_item_id, project_name, description, status, assignee,
                    due_date, action_kind, target_company, allowed_domain,
                    requested_topics, latest_update_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(action_item_id) DO UPDATE SET
                    project_name=excluded.project_name,
                    description=excluded.description,
                    status=excluded.status,
                    assignee=excluded.assignee,
                    due_date=excluded.due_date,
                    action_kind=excluded.action_kind,
                    target_company=excluded.target_company,
                    allowed_domain=excluded.allowed_domain,
                    requested_topics=excluded.requested_topics,
                    latest_update_at=excluded.latest_update_at
                """,
                (
                    data["action_item_id"], data["project_name"],
                    data["description"], data["status"], data["assignee"],
                    data["due_date"], data["action_kind"],
                    data["target_company"], data["allowed_domain"],
                    json.dumps(data["requested_topics"]),
                    data["latest_update_at"],
                ),
            )
            connection.execute(
                "DELETE FROM action_item_evidence WHERE action_item_id = ?",
                (data["action_item_id"],),
            )
            connection.executemany(
                """
                INSERT INTO action_item_evidence (action_item_id, chunk_id)
                VALUES (?, ?)
                """,
                [
                    (data["action_item_id"], chunk_id)
                    for chunk_id in data["evidence_chunk_ids"]
                ],
            )

    def get_action_item(self, action_item_id: UUID) -> ActionItem | None:
        row = self.database.fetch_one(
            "SELECT * FROM action_items WHERE action_item_id = ?",
            (str(action_item_id),),
        )
        if not row:
            return None
        evidence = self.database.fetch_all(
            """
            SELECT chunk_id FROM action_item_evidence
            WHERE action_item_id = ? ORDER BY chunk_id
            """,
            (str(action_item_id),),
        )
        row["requested_topics"] = json.loads(row["requested_topics"])
        row["evidence_chunk_ids"] = [item["chunk_id"] for item in evidence]
        return ActionItem.model_validate(row)

    def list_action_items(self) -> list[ActionItem]:
        rows = self.database.fetch_all(
            "SELECT action_item_id FROM action_items ORDER BY action_item_id"
        )
        items = [
            self.get_action_item(UUID(row["action_item_id"])) for row in rows
        ]
        return [item for item in items if item is not None]

    def count_action_items(self) -> int:
        row = self.database.fetch_one("SELECT COUNT(*) AS count FROM action_items")
        return int(row["count"]) if row else 0

    def save_search_request(self, request: SearchRequest) -> None:
        data = _payload(request)
        self.database.execute(
            """
            INSERT INTO search_requests (
                request_id, query, project_hint, date_from, date_to,
                action_status_filter, top_k, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["request_id"], data["query"], data["project_hint"],
                data["date_from"], data["date_to"],
                data["action_status_filter"], data["top_k"], data["created_at"],
            ),
        )

    def get_search_request(self, request_id: UUID) -> SearchRequest | None:
        row = self.database.fetch_one(
            "SELECT * FROM search_requests WHERE request_id = ?",
            (str(request_id),),
        )
        return SearchRequest.model_validate(row) if row else None

    def save_search_result_item(self, item: SearchResultItem) -> None:
        data = _payload(item)
        self.database.execute(
            """
            INSERT INTO search_result_items (
                result_item_id, request_id, chunk_id, action_item_id, rank,
                score, excerpt, source_title, locator_type, locator_value
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["result_item_id"], data["request_id"], data["chunk_id"],
                data["action_item_id"], data["rank"], data["score"],
                data["excerpt"], data["source_title"], data["locator_type"],
                data["locator_value"],
            ),
        )

    def list_search_result_items(
        self, request_id: UUID
    ) -> list[SearchResultItem]:
        rows = self.database.fetch_all(
            """
            SELECT * FROM search_result_items
            WHERE request_id = ? ORDER BY rank
            """,
            (str(request_id),),
        )
        return [SearchResultItem.model_validate(row) for row in rows]


class VectorKnowledgeStore:
    """Persist semantic vectors separately from Knowledge application data."""

    COLLECTION_NAME = "meeting_content_chunks"

    def __init__(self, data_dir: Path) -> None:
        self._client = QdrantClient(path=str(data_dir / "qdrant"))

    def initialize(self, vector_size: int) -> None:
        if self._client.collection_exists(self.COLLECTION_NAME):
            return
        self._client.create_collection(
            collection_name=self.COLLECTION_NAME,
            vectors_config=models.VectorParams(
                size=vector_size,
                distance=models.Distance.COSINE,
            ),
        )

    def upsert_chunks(self, chunks: Sequence[ContentChunk]) -> None:
        if not chunks:
            return
        self._client.upsert(
            collection_name=self.COLLECTION_NAME,
            wait=True,
            points=[
                models.PointStruct(
                    id=str(chunk.chunk_id),
                    vector=chunk.embedding,
                    payload={
                        "source_id": str(chunk.source_id),
                        "sequence": chunk.sequence,
                        "content": chunk.content,
                        "meeting_date": (
                            chunk.meeting_date.isoformat()
                            if chunk.meeting_date else None
                        ),
                        "project_hints": chunk.project_hints,
                        "locator_type": chunk.locator_type.value,
                        "locator_value": chunk.locator_value,
                        "content_hash": chunk.content_hash,
                    },
                )
                for chunk in chunks
            ],
        )

    def count_chunks(self) -> int:
        if not self._client.collection_exists(self.COLLECTION_NAME):
            return 0
        result = self._client.count(
            collection_name=self.COLLECTION_NAME,
            exact=True,
        )
        return result.count

    def close(self) -> None:
        self._client.close()
