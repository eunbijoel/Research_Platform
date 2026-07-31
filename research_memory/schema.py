from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class TextChunk:
    """Citeable text unit produced by Document Intelligence."""

    text: str
    location: str
    chunk_index: int = 0
    page: int | None = None


@dataclass
class DocumentMetadata:
    """Structured metadata extracted before KB write."""

    title: str = ""
    project_id: str = ""
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    doc_type: str = "unknown"  # proposal | paper | note | meeting | excel | other
    language: str = "ko"
    keywords: list[str] = field(default_factory=list)
    source_filename: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Fact:
    """Lightweight claim / numeric fact with a citeable location."""

    label: str
    value: str
    location: str
    confidence: float = 0.5

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ParsedDocument:
    filename: str
    file_type: str
    full_text: str
    chunks: list[TextChunk]
    metadata: DocumentMetadata
    facts: list[Fact] = field(default_factory=list)
    ok: bool = True
    error: str = ""


@dataclass
class Citation:
    document_id: str
    filename: str
    location: str
    snippet: str
    score: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ChatAnswer:
    answer: str
    citations: list[Citation]
    refused: bool = False
    mode: str = "llm"  # llm | extractive | refused

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "citations": [c.to_dict() for c in self.citations],
            "refused": self.refused,
            "mode": self.mode,
        }
