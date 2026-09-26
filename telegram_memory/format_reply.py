"""Format Memory answers for Telegram (4096-char limit)."""

from __future__ import annotations

from typing import Any, Iterable

TELEGRAM_LIMIT = 4000
MAX_CITATIONS = 5

ROLE_REFERENCE = "reference_document"


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


def _citation_line(index: int, cite: Any, repo: Any | None = None) -> str:
    name = (getattr(cite, "filename", None) or getattr(cite, "document_id", None) or "문서")
    loc = (getattr(cite, "location", None) or "—")
    badge = citation_badge(cite, repo)
    return f"[{index}] {badge} {str(name).strip()} · {str(loc).strip()}"


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
    citations: Iterable[Any] = getattr(result, "citations", None) or []
    lines = [answer]
    cites = list(citations)[:MAX_CITATIONS]
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
