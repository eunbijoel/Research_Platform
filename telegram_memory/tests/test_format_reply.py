from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from format_reply import citation_badge, format_reply, short_source_label


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


def test_short_source_label_date_and_title() -> None:
    label = short_source_label(
        "meeting_minutes_Manufacturing-X_2026-09-08_KMX_Task1_동형암호_용역_개발_시나리오_및_추진방안_협의.docx"
    )
    assert label.startswith("2026-09-08 · ")
    assert "meeting_minutes" not in label.lower()
    assert ".docx" not in label
    assert len(label) < 60


def test_format_reply_strips_markdown_and_shortens_sources() -> None:
    result = SimpleNamespace(
        answer=(
            "KMX는 **동형암호** 용역입니다.\n"
            "\n"
            "* 1차년도 입찰\n"
            "\n"
            "---\n"
            "**사용 근거:**\n"
            "[1], [2] file=meeting_minutes_…docx | location=표 1\n"
        ),
        citations=[
            SimpleNamespace(
                filename=(
                    "meeting_minutes_Manufacturing-X_2026-09-08_"
                    "KMX_Task1_동형암호_용역_개발_시나리오_및_추진방안_협의.docx"
                ),
                location="표 1",
                document_role="project_document",
                document_id="d1",
            ),
            SimpleNamespace(
                filename=(
                    "meeting_minutes_Manufacturing-X_2026-09-08_"
                    "KMX_Task1_동형암호_용역_개발_시나리오_및_추진방안_협의.md"
                ),
                location="전체",
                document_role="project_document",
                document_id="d2",
            ),
        ],
    )
    text = format_reply(result)[0]
    assert "**" not in text
    assert "사용 근거" not in text
    assert "file=" not in text
    assert "표 1" not in text
    assert "[연구문서]" not in text
    assert "KMX는 동형암호 용역입니다." in text
    assert "• 1차년도 입찰" in text
    assert "출처" in text
    assert text.count("[1]") == 1
    assert "2026-09-08" in text
    # docx + md collapse to one short label
    assert "[2]" not in text


def test_format_reply_omits_sources_when_empty() -> None:
    result = SimpleNamespace(answer="메모리에 근거가 없어 답할 수 없습니다.", citations=[])
    text = format_reply(result)[0]
    assert "출처" not in text


def test_format_reply_splits_long_text() -> None:
    result = SimpleNamespace(answer="가" * 5000, citations=[])
    chunks = format_reply(result)
    assert len(chunks) >= 2
    assert all(len(c) <= 4000 for c in chunks)
