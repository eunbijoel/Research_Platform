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
    refused = bool(getattr(result, "refused", False))
    refusal_text = "메모리에 근거가 없어 답할 수 없습니다"
    if refused or answer.startswith(refusal_text):
        # Don't attach noisy RAG citations next to an explicit refusal.
        return _split_telegram(answer)
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
