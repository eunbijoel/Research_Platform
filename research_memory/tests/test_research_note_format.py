from __future__ import annotations

from research_memory.engine.research_note import (
    DEFAULT_ORG,
    DEFAULT_PI,
    DEFAULT_PROJECT_CODE,
    build_research_note_docx,
    build_research_note_hwpx,
    export_basename,
    hwpx_available,
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


def test_export_basename_yymmdd_title_suffix() -> None:
    assert (
        export_basename(date_s="2026-11-24", title="한독 포럼 요약")
        == "261124_한독 포럼 요약_연구노트"
    )
    assert (
        export_basename(date_s="2026-11-24", title="주간 회의", mode="meeting")
        == "261124_주간 회의_회의록"
    )


def test_hwpx_keeps_four_column_grid(tmp_path) -> None:
    if not hwpx_available():
        return
    from hwpx import HwpxDocument

    layout = note_layout(
        created_date="2026-11-24",
        project_code="424C6997",
        experiment_title="한독 포럼 요약",
        content="본문",
    )
    raw = build_research_note_hwpx(title="연구노트", layout=layout)
    assert raw[:2] == b"PK"
    path = tmp_path / "note.hwpx"
    path.write_bytes(raw)
    table = list(HwpxDocument.open(path).tables)[0]
    assert table.column_count == 4
    assert table.row_count == len(layout)
    assert (table.cell(0, 0).text or "").strip() == "작성일자"
    assert (table.cell(0, 2).text or "").strip() == "과제번호"
    assert (table.cell(0, 3).text or "").strip() == "424C6997"
    assert (table.cell(4, 0).text or "").strip() == "실험제목"
    assert (table.cell(4, 1).text or "").strip() == "한독 포럼 요약"
    # Full-width value cell spans three columns.
    span = table.get_cell_map()[4][1].span
    assert span == (1, 3)
