"""Clean, enrich, split, and embed traceable meeting content."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Sequence
from uuid import NAMESPACE_URL, UUID, uuid5

from services.knowledge_service.meeting_knowledge.document_ingestion_role import (
    ExtractedBlock,
)
from shared.contracts import ContentChunk, LocatorType
from shared.openai_gateway import OpenAIGateway


DATE_PATTERN = re.compile(r"(?<!\d)(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)")
MULTI_SPACE_PATTERN = re.compile(r"[ \t\u00a0]+")
PAGE_MARKER_PATTERN = re.compile(r"(?:page|頁)\s*\d+", re.IGNORECASE)
NUMBER_PATTERN = re.compile(r"\d+")

PROJECT_ALIASES = {
    "Display Driver": ("display driver", "顯示驅動"),
    "Color Calibration": ("color calibration", "色彩校正"),
    "Audio Codec": ("audio codec", "音訊編解碼", "音频编解码"),
}


@dataclass(frozen=True)
class CleanedBlock:
    locator_type: LocatorType
    locator_value: str
    order: int
    text: str


@dataclass(frozen=True)
class PreparedChunk:
    chunk_id: UUID
    source_id: UUID
    sequence: int
    content: str
    meeting_date: date | None
    project_hints: list[str]
    locator_type: LocatorType
    locator_value: str
    content_hash: str


@dataclass(frozen=True)
class CleaningResult:
    blocks: list[CleanedBlock]
    warnings: list[str]

    @property
    def character_count(self) -> int:
        return sum(len(block.text) for block in self.blocks)


def _normalized_repeat_key(line: str) -> str:
    normalized = line.casefold()
    normalized = DATE_PATTERN.sub("<date>", normalized)
    normalized = PAGE_MARKER_PATTERN.sub("<page>", normalized)
    normalized = NUMBER_PATTERN.sub("<n>", normalized)
    return MULTI_SPACE_PATTERN.sub(" ", normalized).strip(" |-/")


def _is_format_noise(line: str, repeated_keys: set[str]) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    lowered = stripped.casefold()
    if lowered.startswith("sample |"):
        return True
    if lowered in {"sample", "sample / product development"}:
        return True
    if lowered == "no confidential data":
        return True
    repeat_key = _normalized_repeat_key(stripped)
    repeated_format_marker = (
        "sample" in lowered
        or "confidential" in lowered
        or bool(PAGE_MARKER_PATTERN.search(lowered))
        or bool(re.search(r"\|\s*\d+\s*$", stripped))
    )
    return (
        len(repeat_key) >= 4
        and repeat_key in repeated_keys
        and repeated_format_marker
    )


def clean_meeting_content(blocks: Sequence[ExtractedBlock]) -> CleaningResult:
    line_locations: dict[str, set[tuple[LocatorType, str]]] = defaultdict(set)
    for block in blocks:
        location = (block.locator_type, block.locator_value)
        for line in block.text.splitlines():
            key = _normalized_repeat_key(line)
            if len(key) >= 4:
                line_locations[key].add(location)
    repeated_keys = {
        key for key, locations in line_locations.items() if len(locations) >= 2
    }

    cleaned_blocks: list[CleanedBlock] = []
    warnings: list[str] = []
    for block in sorted(blocks, key=lambda item: item.order):
        cleaned_lines = []
        for raw_line in block.text.replace("\u200b", "").splitlines():
            line = MULTI_SPACE_PATTERN.sub(" ", raw_line).strip()
            if not line or _is_format_noise(line, repeated_keys):
                continue
            cleaned_lines.append(line)
        text = "\n".join(cleaned_lines).strip()
        if not text:
            # A PPTX header/footer is often a standalone shape. Removing a
            # block made entirely of known formatting noise is expected and
            # must not downgrade an otherwise successful document.
            continue
        cleaned_blocks.append(
            CleanedBlock(
                locator_type=block.locator_type,
                locator_value=block.locator_value,
                order=block.order,
                text=text,
            )
        )

    if not cleaned_blocks:
        raise ValueError("document contains no usable content after cleaning")
    return CleaningResult(blocks=cleaned_blocks, warnings=warnings)


def _find_date(text: str) -> date | None:
    for match in DATE_PATTERN.finditer(text):
        try:
            return date(*(int(value) for value in match.groups()))
        except ValueError:
            continue
    return None


def _find_project_hints(text: str) -> list[str]:
    lowered = text.casefold()
    hints = [
        project
        for project, aliases in PROJECT_ALIASES.items()
        if any(alias in lowered for alias in aliases)
    ]
    if "Display Driver" not in hints and re.search(r"\bdd\b", text, re.IGNORECASE):
        hints.insert(0, "Display Driver")
    return hints


def _split_text(text: str, max_characters: int) -> list[str]:
    paragraphs = [part.strip() for part in text.split("\n") if part.strip()]
    chunks: list[str] = []
    current: list[str] = []
    current_length = 0

    def flush() -> None:
        nonlocal current, current_length
        if current:
            chunks.append("\n".join(current))
            current = []
            current_length = 0

    for paragraph in paragraphs:
        if len(paragraph) > max_characters:
            flush()
            for start in range(0, len(paragraph), max_characters):
                chunks.append(paragraph[start:start + max_characters])
            continue
        added_length = len(paragraph) + (1 if current else 0)
        if current and current_length + added_length > max_characters:
            flush()
        current.append(paragraph)
        current_length += added_length
    flush()
    return chunks


def build_traceable_chunks(
    source_id: UUID,
    blocks: Sequence[CleanedBlock],
    *,
    max_characters: int = 900,
) -> list[PreparedChunk]:
    if max_characters < 100:
        raise ValueError("max_characters must be at least 100")

    by_locator: dict[tuple[LocatorType, str], list[CleanedBlock]] = defaultdict(list)
    for block in blocks:
        by_locator[(block.locator_type, block.locator_value)].append(block)

    document_text = "\n".join(block.text for block in blocks)
    document_date = _find_date(document_text)
    document_projects = _find_project_hints(document_text)
    prepared: list[PreparedChunk] = []

    ordered_locations = sorted(
        by_locator.items(),
        key=lambda item: min(block.order for block in item[1]),
    )
    for (locator_type, locator_value), location_blocks in ordered_locations:
        location_text = "\n".join(
            block.text for block in sorted(location_blocks, key=lambda item: item.order)
        )
        # The first date in a later page/slide is commonly an action-item due
        # date. Meeting date is document-level metadata and must remain stable
        # across every locator in the same source.
        meeting_date = document_date
        project_hints = _find_project_hints(location_text) or document_projects
        for content in _split_text(location_text, max_characters):
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            sequence = len(prepared) + 1
            prepared.append(
                PreparedChunk(
                    chunk_id=uuid5(
                        NAMESPACE_URL,
                        f"meeting-chunk:{source_id}:{sequence}:{content_hash}",
                    ),
                    source_id=source_id,
                    sequence=sequence,
                    content=content,
                    meeting_date=meeting_date,
                    project_hints=project_hints,
                    locator_type=locator_type,
                    locator_value=locator_value,
                    content_hash=content_hash,
                )
            )

    if not prepared:
        raise ValueError("document produced no non-empty chunks")
    return prepared


def embed_chunks(
    prepared_chunks: Sequence[PreparedChunk],
    gateway: OpenAIGateway,
    embedding_model: str,
    *,
    batch_size: int = 64,
) -> list[ContentChunk]:
    vectors: list[list[float]] = []
    for start in range(0, len(prepared_chunks), batch_size):
        batch = prepared_chunks[start:start + batch_size]
        response = gateway.create_embeddings(
            model=embedding_model,
            input=[chunk.content for chunk in batch],
        )
        ordered = sorted(response.data, key=lambda item: item.index)
        if len(ordered) != len(batch):
            raise RuntimeError("embedding response count does not match chunk count")
        vectors.extend([list(item.embedding) for item in ordered])

    if len(vectors) != len(prepared_chunks):
        raise RuntimeError("embedding pipeline did not return every chunk")
    if not vectors or not vectors[0]:
        raise RuntimeError("embedding pipeline returned an empty vector")
    vector_size = len(vectors[0])
    if any(len(vector) != vector_size for vector in vectors):
        raise RuntimeError("embedding vectors have inconsistent dimensions")

    return [
        ContentChunk(
            **prepared.__dict__,
            embedding=vector,
            embedding_model=embedding_model,
        )
        for prepared, vector in zip(prepared_chunks, vectors, strict=True)
    ]
