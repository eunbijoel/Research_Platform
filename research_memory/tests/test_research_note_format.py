from __future__ import annotations

from research_memory.engine.research_note import (
    DEFAULT_ORG,
    DEFAULT_PI,
    DEFAULT_PROJECT_CODE,
    build_research_note_docx,
    note_layout,
    parse_research_note_fields,
    research_note_preview_html,
)


def test_parse_distinguishes_summary_and_content() -> None:
    text = """
실험제목:
한독 포럼 정리

일시/장소:
2026-11-24 / KETI

참석자:
강정훈, 조은비

실험내용요약:
포럼 요지 정리

실험내용:
한독 포럼 관련 내용을 정리함

실험참여자:
조은비
"""
    parsed = parse_research_note_fields(text)
    assert parsed["topic"] == "한독 포럼 정리"
    assert "2026-11-24" in parsed["datetime_place"]
    assert "강정훈" in parsed["attendees"]
    assert parsed["summary"] == "포럼 요지 정리"
    assert "한독 포럼 관련 내용을 정리함" in parsed["content"]
    assert parsed["participants"] == "조은비"
    assert "요약" not in parsed["content"]


def test_note_layout_defaults_and_split_rows() -> None:
    layout = note_layout(experiment_title="한독 포럼 정리", content="본문")
    assert layout[0] == [("작성일자", ""), ("과제번호", DEFAULT_PROJECT_CODE)]
    assert layout[3] == [("주관/참여기관", DEFAULT_ORG), ("연구책임자", DEFAULT_PI)]
    assert layout[4] == [("실험제목", "한독 포럼 정리")]
    assert layout[8] == [("실험내용", "본문")]
    labels = [lab for row in layout for lab, _ in row]
    assert "연구노트 작성자" in labels
    assert "확인일자" in labels


def test_docx_and_preview_include_new_fields() -> None:
    layout = note_layout(
        created_date="2026-11-24",
        experiment_title="한독 포럼 정리",
        summary="요약",
        content="본문",
        author="조은비",
    )
    html = research_note_preview_html(layout)
    assert "과제번호" in html
    assert DEFAULT_PROJECT_CODE in html
    assert "실험제목" in html
    assert "한독 포럼 정리" in html
    assert "colspan=\"3\"" in html
    raw = build_research_note_docx(title="연구노트", layout=layout)
    assert raw[:2] == b"PK"
