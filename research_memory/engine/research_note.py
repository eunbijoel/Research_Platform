"""Research / meeting notes: summary → form fields → DOCX/HWPX export.

Adapted from Document_Analyser (writer + summarizer + DOCX/HWPX builders).
"""

from __future__ import annotations

import io
import re
from typing import Sequence

from research_memory.engine.llm import LLMConnectionError, generate_text, llm_available

MODE_RESEARCH = "research"
MODE_MEETING = "meeting"

MODE_LABELS = {
    MODE_RESEARCH: "연구노트",
    MODE_MEETING: "회의록",
}

# Defaults shown on the official research-note template (KETI).
DEFAULT_PROJECT_CODE = "424C6997"
DEFAULT_ORG = "한국전자기술연구원"
DEFAULT_PI = "강정훈"
LABEL_FILL = "BDD7EE"

RESEARCH_NOTE_SECTIONS = """
아래 형식을 그대로 지켜 작성하세요. 각 제목은 반드시 줄의 시작에 쓰고, 마크다운(#, **)은 쓰지 마세요.
원문에 없는 내용은 만들지 말고, 없으면 「확인 필요」라고 쓰세요.

실험제목:

일시/장소:

참석자:

실험내용요약:

실험내용:

실험참여자:
"""

MEETING_NOTE_SECTIONS = """
아래 형식을 그대로 지켜 작성하세요. 각 제목은 반드시 줄의 시작에 쓰고, 마크다운(#, **)은 쓰지 마세요.
녹음 트랜스크립트·회의 자료를 근거로만 작성하고, 없으면 「확인 필요」라고 쓰세요.
Action Item은 가능하면 「담당 / 기한 / 할 일」 한 줄씩 적어 주세요.

회의 제목:

일시:

참석자:

안건:

논의내용:

결정사항:

Action Item:
"""

_SECTION_SPECS: list[tuple[str, str]] = [
    ("topic", r"(?m)^\s*(?:실험\s*제목|주제\s*제안|주\s*제)\s*:?\s*"),
    ("datetime_place", r"(?m)^\s*(?:일시\s*/\s*장소|일\s*시)\s*:?\s*"),
    ("attendees", r"(?m)^\s*참석자\s*:?\s*"),
    ("summary", r"(?m)^\s*(?:실험\s*내용\s*요약|연구\s*결과)\s*:?\s*"),
    # Negative lookahead so "실험내용요약" is not captured as "실험내용".
    ("content", r"(?m)^\s*(?:실험\s*내용(?!\s*요약)|(?:주요\s+)?내용)\s*:?\s*"),
    ("participants", r"(?m)^\s*실험\s*참여자\s*:?\s*"),
    # Legacy headings still accepted from older drafts
    ("purpose", r"(?m)^\s*연구\s*또는\s*작업\s*목적\s*:?\s*"),
    ("materials", r"(?m)^\s*사용한\s*자료(?:\s*및\s*파일)?\s*:?\s*"),
    ("issues", r"(?m)^\s*확인된\s*문제점\s*:?\s*"),
    ("plan", r"(?m)^\s*향후\s*작업\s*계획\s*:?\s*"),
]

_MEETING_SECTION_SPECS: list[tuple[str, str]] = [
    ("topic", r"(?m)^\s*회의\s*제목\s*:?\s*"),
    ("datetime", r"(?m)^\s*일\s*시\s*:?\s*"),
    ("attendees", r"(?m)^\s*참석자\s*:?\s*"),
    ("agenda", r"(?m)^\s*안\s*건\s*:?\s*"),
    ("discussion", r"(?m)^\s*논의\s*내용\s*:?\s*"),
    ("decisions", r"(?m)^\s*결정\s*사항\s*:?\s*"),
    ("actions", r"(?m)^\s*Action\s*Item\s*:?\s*"),
]


def normalize_note_mode(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if raw in {"meeting", "회의록", "minutes", "meeting_minutes"}:
        return MODE_MEETING
    return MODE_RESEARCH


def _parse_sections(
    text: str,
    specs: list[tuple[str, str]],
    out_keys: tuple[str, ...],
    *,
    fallback_key: str,
) -> dict[str, str]:
    raw = (text or "").replace("\r\n", "\n").strip()
    out = {k: "" for k in out_keys}
    if not raw:
        return out

    hits: list[tuple[int, str, int]] = []
    for key, pat in specs:
        m = re.search(pat, raw)
        if m:
            hits.append((m.start(), key, m.end()))
    if not hits:
        out[fallback_key] = raw
        return out

    # Prefer the longer heading when two patterns share a start (e.g. 실험내용 vs 요약).
    best: dict[int, tuple[int, str, int]] = {}
    for start, key, end in hits:
        prev = best.get(start)
        if prev is None or end > prev[2]:
            best[start] = (start, key, end)
    ordered = sorted(best.values(), key=lambda x: x[0])
    for i, (_start, key, end) in enumerate(ordered):
        if key not in out:
            continue
        stop = ordered[i + 1][0] if i + 1 < len(ordered) else len(raw)
        body = raw[end:stop].strip()
        body = re.sub(r"^\s*[-·*]\s*", "", body, count=1)
        out[key] = body.strip()
    return out


def parse_research_note_fields(text: str) -> dict[str, str]:
    """통합 요약에서 실험제목·요약·내용 등 표 필드를 추출."""
    parsed = _parse_sections(
        text,
        _SECTION_SPECS,
        (
            "topic",
            "datetime_place",
            "attendees",
            "summary",
            "content",
            "participants",
            "purpose",
            "materials",
            "issues",
            "plan",
        ),
        fallback_key="content",
    )
    # Older drafts used 목적·문제점·계획 — fold into the new columns when empty.
    if not (parsed.get("summary") or "").strip():
        parsed["summary"] = parsed.get("purpose") or parsed.get("issues") or ""
    if not (parsed.get("content") or "").strip():
        parts = [parsed.get("materials") or "", parsed.get("plan") or ""]
        parsed["content"] = "\n\n".join(p for p in parts if p.strip())
    return parsed


def parse_meeting_note_fields(text: str) -> dict[str, str]:
    """회의록 요약에서 표 필드 추출."""
    return _parse_sections(
        text,
        _MEETING_SECTION_SPECS,
        ("topic", "datetime", "attendees", "agenda", "discussion", "decisions", "actions"),
        fallback_key="discussion",
    )


def suggest_title(filenames: list[str], summary: str, *, mode: str = MODE_RESEARCH) -> str:
    mode = normalize_note_mode(mode)
    prefix = MODE_LABELS[mode]
    if len(filenames) == 1:
        base = re.sub(r"\.[^.]+$", "", filenames[0])
        return f"{prefix} — {base}"
    if filenames:
        return f"{prefix} — {filenames[0]} 외 {len(filenames) - 1}건"
    m = re.search(r"(?:목적|주제|회의\s*제목)[:：]?\s*(.{4,40})", summary)
    if m:
        return f"{prefix} — {m.group(1).strip()}"
    return prefix


def generate_research_note_summary(
    source_text: str,
    *,
    filenames: list[str] | None = None,
    mode: str = MODE_RESEARCH,
) -> str:
    """LLM으로 연구노트/회의록용 통합 요약 생성. 실패 시 원문 앞부분을 반환."""
    text = (source_text or "").strip()
    if not text:
        return ""
    mode = normalize_note_mode(mode)
    names = filenames or []
    file_line = f"참고 파일: {', '.join(names)}\n\n" if names else ""
    if mode == MODE_MEETING:
        prompt = (
            "다음 자료(회의 녹음 트랜스크립트·메모·첨부)를 바탕으로 회의록 초안을 작성하세요.\n\n"
            f"{MEETING_NOTE_SECTIONS}\n\n"
            f"{file_line}"
            f"자료:\n{text[:12000]}"
        )
        fallback = _fallback_meeting_summary
    else:
        prompt = (
            "다음 자료를 바탕으로 연구노트용 통합 요약을 작성하세요.\n\n"
            f"{RESEARCH_NOTE_SECTIONS}\n\n"
            f"{file_line}"
            f"자료:\n{text[:8000]}"
        )
        fallback = _fallback_summary
    if not llm_available():
        return fallback(text, names)
    try:
        out = generate_text(prompt).strip()
        return out or fallback(text, names)
    except LLMConnectionError:
        return fallback(text, names)


def _fallback_summary(text: str, filenames: list[str]) -> str:
    title = suggest_title(filenames, text, mode=MODE_RESEARCH)
    snippet = text.strip().replace("\n", " ")
    if len(snippet) > 600:
        snippet = snippet[:597] + "…"
    materials = ", ".join(filenames) if filenames else "확인 필요"
    return (
        f"실험제목:\n{title}\n\n"
        f"일시/장소:\n확인 필요\n\n"
        f"참석자:\n확인 필요\n\n"
        f"실험내용요약:\n확인 필요\n\n"
        f"실험내용:\n{snippet}\n\n"
        f"(참고 자료: {materials})\n\n"
        f"실험참여자:\n확인 필요"
    )


def _fallback_meeting_summary(text: str, filenames: list[str]) -> str:
    title = suggest_title(filenames, text, mode=MODE_MEETING)
    snippet = text.strip().replace("\n", " ")
    if len(snippet) > 800:
        snippet = snippet[:797] + "…"
    return (
        f"회의 제목:\n{title}\n\n"
        f"일시:\n확인 필요\n\n"
        f"참석자:\n확인 필요\n\n"
        f"안건:\n확인 필요\n\n"
        f"논의내용:\n{snippet}\n\n"
        f"결정사항:\n확인 필요\n\n"
        f"Action Item:\n확인 필요"
    )


_whisper_model = None


def stt_available() -> bool:
    try:
        import faster_whisper  # noqa: F401

        return True
    except ImportError:
        pass
    try:
        import whisper  # noqa: F401

        return True
    except ImportError:
        return False


def transcribe_audio_bytes(data: bytes, filename: str = "audio.wav") -> tuple[str, str]:
    """Best-effort local STT. Returns (transcript, error).

    Prefers ``faster_whisper``, then ``openai-whisper``.
    """
    if not data:
        return "", "빈 오디오 파일입니다."
    if not stt_available():
        return (
            "",
            "자동 받아쓰기를 쓸 수 없습니다. "
            "프로젝트 venv에 `pip install faster-whisper` 후 앱을 재시작하거나, "
            "트랜스크립트를 직접 붙여넣어 주세요.",
        )
    try:
        import tempfile
        from pathlib import Path

        suffix = Path(filename).suffix.lower() or ".wav"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            path = tmp.name
        try:
            err = ""
            try:
                text = _transcribe_with_faster_whisper(path)
            except Exception as exc:  # noqa: BLE001
                text, err = "", f"faster-whisper 변환 실패: {exc}"
            if text:
                return text, ""
            try:
                text = _transcribe_with_whisper(path)
            except Exception as exc:  # noqa: BLE001
                text, err = "", f"whisper 변환 실패: {exc}"
            if text:
                return text, ""
            return "", err or "음성을 텍스트로 변환하지 못했습니다. 파일 형식·길이를 확인해 주세요."
        finally:
            Path(path).unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001
        return "", f"음성 변환 실패: {exc}"


def _transcribe_with_faster_whisper(path: str) -> str:
    global _whisper_model
    from faster_whisper import WhisperModel  # type: ignore

    if _whisper_model is None:
        _whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
    segments, _info = _whisper_model.transcribe(path, language="ko")
    return "\n".join(
        seg.text.strip() for seg in segments if seg.text and seg.text.strip()
    ).strip()


def _transcribe_with_whisper(path: str) -> str:
    import whisper  # type: ignore

    model = whisper.load_model("base")
    result = model.transcribe(path, language="ko")
    return str((result or {}).get("text") or "").strip()


def note_layout(
    *,
    created_date: str = "",
    project_code: str = "",
    project_name: str = "",
    subproject_name: str = "",
    org: str = "",
    pi: str = "",
    experiment_title: str = "",
    datetime_place: str = "",
    attendees: str = "",
    summary: str = "",
    content: str = "",
    participants: str = "",
    author: str = "",
    verifier: str = "",
    author_date: str = "",
    verify_date: str = "",
) -> list[list[tuple[str, str]]]:
    """Official research-note grid. Each inner list is one table row (1 or 2 label/value pairs)."""
    return [
        [
            ("작성일자", created_date or ""),
            ("과제번호", project_code or DEFAULT_PROJECT_CODE),
        ],
        [("과제명", project_name or "")],
        [("세부과제명", subproject_name or "")],
        [
            ("주관/참여기관", org or DEFAULT_ORG),
            ("연구책임자", pi or DEFAULT_PI),
        ],
        [("실험제목", experiment_title or "")],
        [("일시/장소", datetime_place or "")],
        [("참석자", attendees or "")],
        [("실험내용요약", summary or "")],
        [("실험내용", content or "")],
        [("실험참여자", participants or "")],
        [
            ("연구노트 작성자", author or ""),
            ("확인자", verifier or ""),
        ],
        [
            ("작성일자", author_date or created_date or ""),
            ("확인일자", verify_date or ""),
        ],
    ]


def note_rows(
    *,
    topic: str = "",
    owner: str = "",
    date_s: str = "",
    author: str = "",
    content: str = "",
    results: str = "",
    etc: str = "",
    created_date: str = "",
    project_code: str = "",
    project_name: str = "",
    subproject_name: str = "",
    org: str = "",
    pi: str = "",
    experiment_title: str = "",
    datetime_place: str = "",
    attendees: str = "",
    summary: str = "",
    participants: str = "",
    verifier: str = "",
    author_date: str = "",
    verify_date: str = "",
) -> list[tuple[str, str]]:
    """Flattened label/value pairs (markdown / simple callers)."""
    layout = note_layout(
        created_date=created_date or date_s,
        project_code=project_code,
        project_name=project_name,
        subproject_name=subproject_name,
        org=org,
        pi=pi or owner,
        experiment_title=experiment_title or topic,
        datetime_place=datetime_place or date_s,
        attendees=attendees,
        summary=summary or results,
        content=content if content else etc,
        participants=participants,
        author=author,
        verifier=verifier,
        author_date=author_date or created_date or date_s,
        verify_date=verify_date,
    )
    flat: list[tuple[str, str]] = []
    for row in layout:
        flat.extend(row)
    return flat


def meeting_rows(
    *,
    topic: str,
    date_s: str,
    attendees: str,
    agenda: str,
    discussion: str,
    decisions: str,
    actions: str,
) -> list[tuple[str, str]]:
    return [
        ("회의 제목", topic or ""),
        ("일 시", date_s or ""),
        ("참석자", attendees or ""),
        ("안 건", agenda or ""),
        ("논의내용", discussion or ""),
        ("결정사항", decisions or ""),
        ("Action Item", actions or ""),
    ]


def note_as_markdown(
    *,
    topic: str = "",
    owner: str = "",
    date_s: str = "",
    author: str = "",
    content: str = "",
    results: str = "",
    etc: str = "",
    project_id: str = "",
    created_date: str = "",
    project_code: str = "",
    project_name: str = "",
    subproject_name: str = "",
    org: str = "",
    pi: str = "",
    experiment_title: str = "",
    datetime_place: str = "",
    attendees: str = "",
    summary: str = "",
    participants: str = "",
    verifier: str = "",
    author_date: str = "",
    verify_date: str = "",
) -> str:
    """Plain markdown twin of the research-note table (good for Memory ingest)."""
    title = experiment_title or topic or "제목 없음"
    lines = [
        f"# 연구노트 — {title}",
        "",
    ]
    if project_id:
        lines.append(f"Project: {project_id}")
        lines.append("")
    for label, val in note_rows(
        topic=topic,
        owner=owner,
        date_s=date_s,
        author=author,
        content=content,
        results=results,
        etc=etc,
        created_date=created_date,
        project_code=project_code,
        project_name=project_name,
        subproject_name=subproject_name,
        org=org,
        pi=pi,
        experiment_title=experiment_title,
        datetime_place=datetime_place,
        attendees=attendees,
        summary=summary,
        participants=participants,
        verifier=verifier,
        author_date=author_date,
        verify_date=verify_date,
    ):
        lines.append(f"## {label.strip()}")
        lines.append(val.strip() or "확인 필요")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def meeting_as_markdown(
    *,
    topic: str,
    date_s: str,
    attendees: str,
    agenda: str,
    discussion: str,
    decisions: str,
    actions: str,
    project_id: str = "",
    recording_name: str = "",
) -> str:
    """Plain markdown twin of the meeting-minutes table."""
    lines = [
        f"# 회의록 — {topic or '제목 없음'}",
        "",
    ]
    if project_id:
        lines.append(f"Project: {project_id}")
        lines.append("")
    if recording_name:
        lines.append(f"Recording: {recording_name}")
        lines.append("")
    for label, val in meeting_rows(
        topic=topic,
        date_s=date_s,
        attendees=attendees,
        agenda=agenda,
        discussion=discussion,
        decisions=decisions,
        actions=actions,
    ):
        lines.append(f"## {label.strip()}")
        lines.append(val.strip() or "확인 필요")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _normalize_note_rows(rows: Sequence[tuple[str, str]]) -> list[tuple[str, str]]:
    out = [(str(label or ""), str(val or "")) for label, val in rows]
    return out or [("실험내용", " ")]


def _normalize_note_layout(
    layout: Sequence[Sequence[tuple[str, str]]] | None,
    rows: Sequence[tuple[str, str]] | None = None,
) -> list[list[tuple[str, str]]]:
    if layout:
        out: list[list[tuple[str, str]]] = []
        for row in layout:
            cells = [(str(label or ""), str(val or "")) for label, val in row]
            if cells:
                out.append(cells)
        return out or [[("실험내용", " ")]]
    flat = _normalize_note_rows(rows or [])
    return [[pair] for pair in flat]


def _strip_md_noise(text: str) -> str:
    out_lines: list[str] = []
    for ln in (text or "").splitlines():
        s = ln.rstrip()
        s = re.sub(r"^#{1,6}\s*", "", s)
        s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
        s = re.sub(r"__(.+?)__", r"\1", s)
        s = re.sub(r"`([^`]+)`", r"\1", s)
        if re.fullmatch(r"-{3,}|_{3,}|\*{3,}", s.strip()):
            continue
        out_lines.append(s)
    return "\n".join(out_lines)


def _shade_cell(cell: object, fill: str = LABEL_FILL) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    try:
        shading = OxmlElement("w:shd")
        shading.set(qn("w:fill"), fill)
        shading.set(qn("w:val"), "clear")
        cell._tc.get_or_add_tcPr().append(shading)  # type: ignore[attr-defined]
    except Exception:
        pass


def _write_docx_cell(cell: object, text: str, *, label: bool = False) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    cell.text = text if str(text).strip() else (" " if not label else text)
    if label:
        _shade_cell(cell)
        for p in cell.paragraphs:  # type: ignore[attr-defined]
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            for run in p.runs:
                run.bold = True


def build_research_note_docx(
    rows: Sequence[tuple[str, str]] | None = None,
    *,
    title: str = "연구노트",
    layout: Sequence[Sequence[tuple[str, str]]] | None = None,
) -> bytes:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt

    grid = _normalize_note_layout(layout, rows)
    d = Document()
    h = d.add_paragraph(title)
    h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if h.runs:
        h.runs[0].bold = True
        h.runs[0].font.size = Pt(16)

    table = d.add_table(rows=len(grid), cols=4)
    table.style = "Table Grid"
    table.autofit = False
    widths = (Cm(3.0), Cm(5.2), Cm(3.0), Cm(5.2))
    for idx, width in enumerate(widths):
        table.columns[idx].width = width

    for i, cells in enumerate(grid):
        row = table.rows[i]
        if len(cells) == 1:
            label, val = cells[0]
            _write_docx_cell(row.cells[0], label, label=True)
            merged = row.cells[1].merge(row.cells[3])
            _write_docx_cell(merged, _strip_md_noise(val))
        else:
            (l1, v1), (l2, v2) = cells[0], cells[1]
            _write_docx_cell(row.cells[0], l1, label=True)
            _write_docx_cell(row.cells[1], _strip_md_noise(v1))
            _write_docx_cell(row.cells[2], l2, label=True)
            _write_docx_cell(row.cells[3], _strip_md_noise(v2))
        # Tall body row
        if any(lab in {"실험내용", "내 용"} for lab, _ in cells):
            try:
                tr = row._tr
                trPr = tr.get_or_add_trPr()
                from docx.oxml import OxmlElement
                from docx.oxml.ns import qn

                height = OxmlElement("w:trHeight")
                height.set(qn("w:val"), "2400")
                height.set(qn("w:hRule"), "atLeast")
                trPr.append(height)
            except Exception:
                pass

    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def research_note_preview_html(
    layout: Sequence[Sequence[tuple[str, str]]],
    *,
    title: str = "연구노트",
) -> str:
    """HTML twin of the official 4-column research-note grid."""
    import html as html_lib

    tall = {"실험내용", "실험내용요약", "내 용"}
    body: list[str] = []
    for cells in layout:
        if len(cells) == 1:
            label, val = cells[0]
            min_h = "140px" if label in tall else ("72px" if label == "실험내용요약" else "34px")
            body.append(
                f"""<tr>
  <th style="width:18%;background:#{LABEL_FILL};text-align:center;vertical-align:middle;
             border:1px solid #333;padding:8px;font-weight:600;">{html_lib.escape(label)}</th>
  <td colspan="3" style="border:1px solid #333;padding:8px;vertical-align:top;min-height:{min_h};
             white-space:pre-wrap;">{html_lib.escape(val)}</td>
</tr>"""
            )
        else:
            (l1, v1), (l2, v2) = cells[0], cells[1]
            body.append(
                f"""<tr>
  <th style="width:18%;background:#{LABEL_FILL};text-align:center;vertical-align:middle;
             border:1px solid #333;padding:8px;font-weight:600;">{html_lib.escape(l1)}</th>
  <td style="width:32%;border:1px solid #333;padding:8px;vertical-align:top;
             white-space:pre-wrap;">{html_lib.escape(v1)}</td>
  <th style="width:18%;background:#{LABEL_FILL};text-align:center;vertical-align:middle;
             border:1px solid #333;padding:8px;font-weight:600;">{html_lib.escape(l2)}</th>
  <td style="width:32%;border:1px solid #333;padding:8px;vertical-align:top;
             white-space:pre-wrap;">{html_lib.escape(v2)}</td>
</tr>"""
            )
    return f"""
<div style="font-family:'Malgun Gothic','맑은 고딕',sans-serif;color:#111;">
  <div style="text-align:center;font-size:22px;font-weight:700;margin:8px 0 14px;">{html_lib.escape(title)}</div>
  <table style="width:100%;border-collapse:collapse;table-layout:fixed;">
    {''.join(body)}
  </table>
</div>
"""


def export_basename(*, date_s: str, title: str, mode: str = MODE_RESEARCH) -> str:
    """Download stem: ``yymmdd_제목_연구노트`` (or ``_회의록``)."""
    mode = normalize_note_mode(mode)
    digits = re.sub(r"\D", "", date_s or "")
    if len(digits) >= 8:
        yymmdd = digits[-8:][2:]
    elif len(digits) == 6:
        yymmdd = digits
    else:
        from datetime import date as _date

        yymmdd = _date.today().strftime("%y%m%d")
    raw_title = (title or "").strip() or MODE_LABELS[mode]
    slug = re.sub(r'[\\/:*?"<>|]+', " ", raw_title)
    slug = re.sub(r"\s+", " ", slug).strip()[:60] or MODE_LABELS[mode]
    return f"{yymmdd}_{slug}_{MODE_LABELS[mode]}"


def build_research_note_hwpx(
    rows: Sequence[tuple[str, str]] | None = None,
    *,
    title: str = "연구노트",
    layout: Sequence[Sequence[tuple[str, str]]] | None = None,
) -> bytes:
    """Optional HWPX export when python-hwpx is installed.

    Uses the same 4-column grid as DOCX/HTML (split rows + merged value cells).
    """
    from hwpx import HwpxDocument  # type: ignore

    grid = _normalize_note_layout(layout, rows)
    doc = HwpxDocument.new()
    paras = list(getattr(doc, "paragraphs", None) or [])
    if paras:
        try:
            paras[0].text = title
        except Exception:
            doc.add_paragraph(title)
    else:
        doc.add_paragraph(title)

    table = doc.add_table(rows=len(grid), cols=4, width=45_000)
    try:
        table.set_column_widths([2200, 3900, 2200, 3900])
    except Exception:
        pass

    def _label(r: int, c: int, text: str) -> None:
        table.set_cell_text(r, c, text if text else " ")
        try:
            table.set_cell_shading(r, c, f"#{LABEL_FILL}")
        except Exception:
            pass

    def _value(r: int, c: int, text: str) -> None:
        cleaned = _strip_md_noise(text)
        table.set_cell_text(
            r,
            c,
            cleaned if cleaned.strip() else " ",
            logical=True,
            split_paragraphs=True,
        )

    for i, cells in enumerate(grid):
        if len(cells) == 1:
            label, val = cells[0]
            _label(i, 0, label)
            try:
                table.merge_cells(i, 1, i, 3)
            except Exception:
                pass
            _value(i, 1, val)
        else:
            (l1, v1), (l2, v2) = cells[0], cells[1]
            _label(i, 0, l1)
            _value(i, 1, v1)
            _label(i, 2, l2)
            _value(i, 3, v2)

    raw = doc.to_bytes()
    if not raw or raw[:2] != b"PK":
        raise RuntimeError("연구노트 HWPX 생성 실패")
    return raw


def hwpx_available() -> bool:
    try:
        import hwpx  # noqa: F401

        return True
    except Exception:
        return False
