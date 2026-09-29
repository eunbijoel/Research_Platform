"""Format Memory answers for Telegram (4096-char limit)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

TELEGRAM_LIMIT = 4000
MAX_CITATIONS = 3

ROLE_REFERENCE = "reference_document"

_DATE_IN_NAME = re.compile(r"(20\d{2}-\d{2}-\d{2})")
_MD_BOLD = re.compile(r"\*\*(.+?)\*\*")
_MD_ITALIC = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_MD_BOLD_US = re.compile(r"__(.+?)__")
_MD_ITALIC_US = re.compile(r"(?<!_)_(?!_)(.+?)(?<!_)_(?!_)")
_MD_HEADER = re.compile(r"(?m)^#{1,6}\s+")
_MD_BULLET = re.compile(r"(?m)^\s*[-*]\s+")
# LLM often dumps Evidence metadata / "사용 근거" after the real answer.
_ANSWER_CUT = re.compile(
    r"(?is)\n\s*(?:---+)\s*\n|"
    r"\n\s*\*?\*?사용\s*근거\*?\*?\s*:?\s*\n|"
    r"\n\s*출처\s*\n|"
    r"\n\s*\[\d+\][^\n]*\bfile\s*="
)
_NOISE_PREFIXES = (
    "meeting_minutes_",
    "meeting_minute_",
    "minutes_",
    "manufacturing-x_",
    "manufacturing_x_",
    "manufacturingx_",
)


def citation_badge(cite: Any, repo: Any | None = None) -> str:
    doc = None
    doc_id = getattr(cite, "document_id", None)
    if repo is not None and doc_id:
        getter = getattr(repo, "get_document", None)
        if callable(getter):
            doc = getter(str(doc_id))
    dtype = (doc or {}).get("doc_type") if isinstance(doc, dict) else None
    role = getattr(cite, "document_role", None) or (
        (doc or {}).get("document_role") if isinstance(doc, dict) else None
    )
    if role == ROLE_REFERENCE:
        return "[참고규정]" if dtype == "regulation" else "[참고자료]"
    return "[연구문서]"


def short_source_label(filename: str, *, title: str | None = None) -> str:
    """Compact Telegram source: date + short title (no extension / location)."""
    raw = (title or filename or "문서").strip() or "문서"
    stem = Path(raw).stem if raw.endswith((".md", ".docx", ".hwpx", ".hwp", ".pdf", ".txt")) else raw
    day = None
    m = _DATE_IN_NAME.search(stem)
    if m:
        day = m.group(1)
        stem = stem.replace(m.group(1), " ", 1)
    cleaned = stem
    # Peel common filename prefixes repeatedly (meeting_minutes_ + Manufacturing-X_).
    changed = True
    while changed:
        changed = False
        lowered = cleaned.lower().lstrip(" _-")
        cleaned = cleaned.lstrip(" _-")
        for prefix in _NOISE_PREFIXES:
            if lowered.startswith(prefix):
                cleaned = cleaned[len(prefix) :]
                changed = True
                break
    cleaned = re.sub(r"[_\s]+", " ", cleaned).strip(" -_.")
    if len(cleaned) > 36:
        cleaned = cleaned[:34].rstrip(" .…") + "…"
    if day and cleaned:
        return f"{day} · {cleaned}"
    if day:
        return day
    return cleaned or "문서"


def _citation_label(cite: Any, repo: Any | None = None) -> str:
    filename = str(
        getattr(cite, "filename", None) or getattr(cite, "document_id", None) or "문서"
    )
    title = None
    doc_id = getattr(cite, "document_id", None)
    if repo is not None and doc_id:
        getter = getattr(repo, "get_document", None)
        if callable(getter):
            doc = getter(str(doc_id))
            if isinstance(doc, dict):
                title = str(doc.get("title") or "").strip() or None
    return short_source_label(filename, title=title)


def _citation_line(index: int, cite: Any, repo: Any | None = None) -> str:
    return f"[{index}] {_citation_label(cite, repo)}"


def _strip_markdown(text: str) -> str:
    text = _MD_BOLD.sub(r"\1", text)
    text = _MD_BOLD_US.sub(r"\1", text)
    text = _MD_ITALIC.sub(r"\1", text)
    text = _MD_ITALIC_US.sub(r"\1", text)
    text = _MD_HEADER.sub("", text)
    text = _MD_BULLET.sub("• ", text)
    return text


def _trim_answer_body(answer: str) -> str:
    """Drop LLM-appended evidence dumps; keep the prose only."""
    text = (answer or "").strip()
    m = _ANSWER_CUT.search(text)
    if m:
        text = text[: m.start()].rstrip()
    # Drop a trailing bare "[1], [2], [3]" line (footer covers sources).
    text = re.sub(r"(?m)\n\s*\[\d+\](?:\s*[,·/]\s*\[\d+\])*\s*$", "", text).rstrip()
    return text


def _prepare_answer(answer: str) -> str:
    return _strip_markdown(_trim_answer_body(answer)).strip() or "(빈 답변)"


def _dedupe_citations(cites: list[Any], *, repo: Any | None = None) -> list[Any]:
    seen: set[str] = set()
    out: list[Any] = []
    for cite in cites:
        label = _citation_label(cite, repo)
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(cite)
        if len(out) >= MAX_CITATIONS:
            break
    return out


def _split_telegram(text: str) -> list[str]:
    text = (text or "").strip()
    if not text:
        return ["(빈 답변)"]
    if len(text) <= TELEGRAM_LIMIT:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in text.split("\n"):
        extra = len(line) + (1 if current else 0)
        if current and current_len + extra > TELEGRAM_LIMIT:
            chunks.append("\n".join(current))
            current = [line]
            current_len = len(line)
            continue
        if not current:
            current = [line[:TELEGRAM_LIMIT]]
            current_len = len(current[0])
            rest = line[TELEGRAM_LIMIT:]
            while rest:
                chunks.append("\n".join(current))
                current = [rest[:TELEGRAM_LIMIT]]
                current_len = len(current[0])
                rest = rest[TELEGRAM_LIMIT:]
            continue
        current.append(line)
        current_len += extra
    if current:
        chunks.append("\n".join(current))
    return chunks or ["(빈 답변)"]


def format_reply(result: Any, *, repo: Any | None = None) -> list[str]:
    answer = (getattr(result, "answer", None) or "").strip() or "(빈 답변)"
    refused = bool(getattr(result, "refused", False))
    refusal_text = "메모리에 근거가 없어 답할 수 없습니다"
    if refused or answer.startswith(refusal_text):
        # Don't attach noisy RAG citations next to an explicit refusal.
        return _split_telegram(_prepare_answer(answer))
    citations: Iterable[Any] = getattr(result, "citations", None) or []
    body = _prepare_answer(answer)
    lines = [body]
    cites = _dedupe_citations(list(citations), repo=repo)
    if cites:
        lines.append("")
        lines.append("출처")
        for i, cite in enumerate(cites, start=1):
            lines.append(_citation_line(i, cite, repo))
    return _split_telegram("\n".join(lines))


def _schedule_item_line(item: dict[str, Any]) -> str:
    try:
        from research_memory.engine.schedule import (
            chip_time_and_title,
            event_type_label,
            status_label,
        )
    except Exception:  # noqa: BLE001
        chip_time_and_title = None  # type: ignore[assignment]
        event_type_label = lambda v: str(v or "작업")  # noqa: E731
        status_label = lambda v: str(v or "예정")  # noqa: E731

    title_raw = str(item.get("title") or "").strip() or "(제목 없음)"
    if chip_time_and_title is not None:
        time_str, base_title = chip_time_and_title(title_raw, item.get("note"))
    else:
        time_str, base_title = None, title_raw
    etype = event_type_label(item.get("event_type"))
    status = status_label(item.get("status"))
    project = str(item.get("project_id") or "").strip() or "—"
    start = str(item.get("date") or "").strip()[:10]
    end = str(item.get("end_date") or "").strip()[:10]
    if end and end != start:
        day = f"{start}~{end}"
    else:
        day = start or "—"
    when = f"{time_str} " if time_str else ""
    return f"• {day} {when}{base_title} [{etype}/{status}] · {project}"


def format_schedule(query: Any) -> list[str]:
    label = getattr(query, "label", None) or "일정"
    items: list[dict[str, Any]] = list(getattr(query, "items", None) or [])
    lines = [f"📅 {label}"]
    if not items:
        lines.append("등록된 일정이 없습니다.")
        return _split_telegram("\n".join(lines))
    for item in items:
        if isinstance(item, dict):
            lines.append(_schedule_item_line(item))
    return _split_telegram("\n".join(lines))


def format_holidays(query: Any) -> list[str]:
    label = getattr(query, "label", None) or "공휴일"
    items = list(getattr(query, "items", None) or [])
    lines = [
        f"🎌 {label}",
        "(한국 법정 공휴일 캘린더 기준 · Memory 문서 아님)",
    ]
    if not items:
        lines.append("해당하는 공휴일을 찾지 못했습니다.")
        return _split_telegram("\n".join(lines))
    for hit in items:
        day = getattr(hit, "day", None)
        name = getattr(hit, "name", None)
        if day is None and isinstance(hit, dict):
            day = hit.get("day")
            name = hit.get("name")
        day_s = day.isoformat() if hasattr(day, "isoformat") else str(day or "—")
        lines.append(f"• {day_s} — {name or '공휴일'}")
    return _split_telegram("\n".join(lines))


def format_projects(inventory: Any) -> list[str]:
    projects = list(getattr(inventory, "projects", None) or [])
    total_docs = int(getattr(inventory, "total_documents", 0) or 0)
    lines = [
        f"📁 프로젝트 {len(projects)}개 · 문서 {total_docs}건",
        "(Memory 등록 폴더 기준 · 읽기 전용)",
    ]
    if not projects:
        lines.append("등록된 프로젝트가 없습니다.")
        return _split_telegram("\n".join(lines))
    for p in projects:
        pid = getattr(p, "project_id", None) or (p.get("project_id") if isinstance(p, dict) else "")
        title = getattr(p, "title", None) or (p.get("title") if isinstance(p, dict) else "") or pid
        n_docs = getattr(p, "document_count", None)
        if n_docs is None and isinstance(p, dict):
            n_docs = p.get("document_count", 0)
        last = getattr(p, "last_activity", None) or (
            p.get("last_activity") if isinstance(p, dict) else ""
        ) or "—"
        if title and title != pid:
            lines.append(f"• {pid} — {title}")
        else:
            lines.append(f"• {pid}")
        lines.append(f"  문서 {int(n_docs or 0)}건 · 최근 {last}")
    return _split_telegram("\n".join(lines))


def format_latest(folder: Any | None) -> list[str]:
    if folder is None:
        return _split_telegram("최신 작업 폴더를 찾지 못했습니다.")
    pid = getattr(folder, "project_id", "") or ""
    title = getattr(folder, "title", "") or pid
    last = getattr(folder, "last_activity", "") or "—"
    n_docs = int(getattr(folder, "document_count", 0) or 0)
    latest_doc = getattr(folder, "latest_document_title", "") or ""
    lines = [
        "🆕 제일 최근 활동 폴더",
        f"• {pid}" + (f" — {title}" if title and title != pid else ""),
        f"  최근 활동: {last}",
        f"  문서 {n_docs}건",
    ]
    if latest_doc:
        lines.append(f"  최신 문서: {latest_doc}")
    return _split_telegram("\n".join(lines))


def format_meeting(ctx: Any | None, *, heading: str = "🗓️ 최근 회의") -> list[str]:
    if ctx is None:
        return _split_telegram("일정에서 회의를 찾지 못했습니다.")
    item = getattr(ctx, "item", None) or {}
    attachments = list(getattr(ctx, "attachments", None) or [])
    lines = [heading]
    if isinstance(item, dict):
        lines.append(_schedule_item_line(item))
        note = str(item.get("note") or "").strip()
        if note:
            lines.append(f"  메모: {note[:200]}")
    if not attachments:
        lines.append("  첨부 회의록: 없음")
    else:
        lines.append(f"  첨부 회의록 {len(attachments)}건:")
        for att in attachments[:8]:
            if not isinstance(att, dict):
                continue
            name = str(att.get("title") or att.get("filename") or att.get("id") or "문서")
            status = str(att.get("status") or "")
            suffix = f" ({status})" if status and status != "ready" else ""
            lines.append(f"  · {name}{suffix}")
    return _split_telegram("\n".join(lines))
