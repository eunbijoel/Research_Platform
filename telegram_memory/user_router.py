"""Shared question → reply chunks for Bot and Telethon user MVP.

Keeps MemoryService + format_reply reuse without Telegram SDK imports.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from format_reply import (
    _split_telegram,
    format_holidays,
    format_latest,
    format_meeting,
    format_projects,
    format_reply,
    format_schedule,
)
from service import detect_inventory_intent, detect_schedule_intent, parse_mentioned_month

if TYPE_CHECKING:
    from service import MemoryService

logger = logging.getLogger("research-memory-bot")

_COMMAND_ALIASES = {
    "/projects": "프로젝트 목록",
    "/latest": "최근 활동 폴더",
    "/today": "오늘 일정",
    "/week": "이번 주 일정",
    "/month": "이번 달 일정",
}


def normalize_question(text: str) -> str:
    """Map slash commands to NL so intent detectors can fire."""
    q = (text or "").strip()
    if not q:
        return ""
    first = q.split(maxsplit=1)[0].lower()
    # /today@BotName → /today
    if "@" in first:
        first = first.split("@", 1)[0]
    if first in _COMMAND_ALIASES:
        rest = q[len(q.split(maxsplit=1)[0]) :].strip()
        base = _COMMAND_ALIASES[first]
        return f"{base} {rest}".strip() if rest else base
    return q


def answer_question_chunks(memory: MemoryService, question: str) -> list[str]:
    """Resolve one user question into Telegram-sized text chunks (sync)."""
    q = normalize_question(question)
    if not q:
        return ["질문이 비어 있습니다."]

    try:
        inv = detect_inventory_intent(q)
        if inv == "projects":
            return format_projects(memory.list_project_inventory())
        if inv == "latest":
            return format_latest(memory.latest_folder())

        sched = detect_schedule_intent(q)
        if sched == "today":
            return format_schedule(memory.list_today())
        if sched == "week":
            return format_schedule(memory.list_week())
        if sched == "month":
            target = parse_mentioned_month(q)
            return format_schedule(memory.list_month(target))
        if sched == "holiday":
            return format_holidays(memory.lookup_holidays(q))
        if sched == "last_meeting":
            return format_meeting(memory.latest_meeting())
        if sched == "meeting_notes":
            meeting = memory.resolve_meeting(q)
            result = memory.ask_meeting_notes(q, meeting)
            header = format_meeting(meeting, heading="🗓️ 대상 회의")
            answer = format_reply(result, repo=memory.repo)
            if not answer:
                return header
            combined = f"{header[0]}\n\n{answer[0]}"
            return _split_telegram(combined) + header[1:] + answer[1:]

        result = memory.ask(q)
        return format_reply(result, repo=memory.repo)
    except Exception:
        logger.exception("user_router failed question=%r", q[:120])
        return ["Memory에 연결하지 못했습니다."]
