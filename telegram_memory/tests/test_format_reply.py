from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from format_reply import citation_badge, format_reply


def test_project_doc_badge() -> None:
    cite = SimpleNamespace(document_role="project_document", document_id="d1")
    assert citation_badge(cite) == "[연구문서]"


def test_reference_doc_badge() -> None:
    cite = SimpleNamespace(document_role="reference_document", document_id="d2")
    assert citation_badge(cite) == "[참고자료]"


def test_regulation_badge_from_repo() -> None:
    cite = SimpleNamespace(document_role="reference_document", document_id="d3")

    class FakeRepo:
        def get_document(self, doc_id: str) -> dict:
            return {"doc_type": "regulation", "document_role": "reference_document"}

    assert citation_badge(cite, FakeRepo()) == "[참고규정]"


def test_format_reply_includes_sources() -> None:
    result = SimpleNamespace(
        answer="근거에 따라 답합니다.",
        citations=[
            SimpleNamespace(
                filename="note.md",
                location="p.2",
                document_role="project_document",
                document_id="d1",
            )
        ],
    )
    chunks = format_reply(result)
    assert len(chunks) == 1
    assert "근거에 따라 답합니다." in chunks[0]
    assert "출처" in chunks[0]
    assert "[1] [연구문서] note.md · p.2" in chunks[0]


def test_format_reply_omits_sources_when_empty() -> None:
    result = SimpleNamespace(answer="메모리에 근거가 없어 답할 수 없습니다.", citations=[])
    text = format_reply(result)[0]
    assert "출처" not in text


def test_format_reply_splits_long_text() -> None:
    result = SimpleNamespace(answer="가" * 5000, citations=[])
    chunks = format_reply(result)
    assert len(chunks) >= 2
    assert all(len(c) <= 4000 for c in chunks)
