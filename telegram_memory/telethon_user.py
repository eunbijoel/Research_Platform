"""Minimal Telethon user-account MVP.

Listens on allowlisted chats, answers via MemoryService (same router as Bot logic),
replies with the logged-in user account. Does not scrape history or write Memory.
Bot API path (telegram_bot.py) is unchanged and separate.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("research-memory-bot")

ROOT = Path(__file__).resolve().parent
DEFAULT_SESSION = ROOT / "data" / "telethon.session"


@dataclass(frozen=True)
class TelethonSettings:
    api_id: int
    api_hash: str
    session_path: Path
    chat_ids: frozenset[int]
    trigger: str  # empty = respond to every new text in allowlisted chats


def load_telethon_settings() -> TelethonSettings:
    from app import _parse_id_set, load_env_files

    load_env_files()
    api_id_raw = (os.getenv("TELETHON_API_ID") or "").strip()
    api_hash = (os.getenv("TELETHON_API_HASH") or "").strip()
    if not api_id_raw or not api_hash:
        raise SystemExit(
            "missing TELETHON_API_ID / TELETHON_API_HASH\n"
            "get them at https://my.telegram.org/apps and set in telegram_memory/.env"
        )
    try:
        api_id = int(api_id_raw)
    except ValueError as exc:
        raise SystemExit(f"invalid TELETHON_API_ID: {api_id_raw!r}") from exc

    chat_ids = _parse_id_set("TELETHON_CHAT_IDS")
    if not chat_ids:
        raise SystemExit(
            "missing TELETHON_CHAT_IDS\n"
            "set one or more chat ids (comma-separated), e.g. -100xxxxxxxxxx"
        )

    session_raw = (os.getenv("TELETHON_SESSION") or "").strip()
    session_path = Path(session_raw) if session_raw else DEFAULT_SESSION
    if not session_path.is_absolute():
        session_path = (ROOT / session_path).resolve()
    session_path.parent.mkdir(parents=True, exist_ok=True)

    trigger = (os.getenv("TELETHON_TRIGGER") or "").strip()
    return TelethonSettings(
        api_id=api_id,
        api_hash=api_hash,
        session_path=session_path,
        chat_ids=chat_ids,
        trigger=trigger,
    )


def _strip_trigger(text: str, trigger: str) -> str | None:
    """Return question without trigger, or None if trigger required but missing."""
    q = (text or "").strip()
    if not trigger:
        return q
    t = trigger.strip()
    # "trigger: question" / "trigger question" first (avoids leaving a leading ':')
    m = re.match(
        re.escape(t) + r"(?:\s*[:\-–—]\s*|\s+)(.*)$",
        q,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if m:
        return (m.group(1) or "").strip()
    if q.lower().startswith(t.lower()):
        return q[len(t) :].lstrip(" :,-–—\t").strip()
    return None


def _chat_id_from_event(event: Any) -> int | None:
    chat_id = getattr(event, "chat_id", None)
    if isinstance(chat_id, int):
        return chat_id
    chat = getattr(event, "chat", None)
    if chat is not None and hasattr(chat, "id"):
        try:
            return int(chat.id)
        except (TypeError, ValueError):
            return None
    return None


async def run_telethon_user() -> int:
    try:
        from telethon import TelegramClient, events
    except ImportError as exc:
        raise SystemExit(
            "telethon is not installed. run:\n"
            "  pip install -r telegram_memory/requirements.txt"
        ) from exc

    from service import MemoryService
    from user_router import answer_question_chunks

    settings = load_telethon_settings()
    memory = MemoryService()
    session = str(settings.session_path)
    # Telethon appends .session itself if needed; pass path without forcing suffix twice
    if session.endswith(".session"):
        session_base = session[: -len(".session")]
    else:
        session_base = session

    client = TelegramClient(session_base, settings.api_id, settings.api_hash)
    chats = list(settings.chat_ids)

    @client.on(events.NewMessage(chats=chats if chats else None))
    async def on_new_message(event: Any) -> None:  # noqa: ANN401
        if getattr(event, "out", False):
            return
        msg = event.message
        if msg is None:
            return
        text = (getattr(msg, "message", None) or getattr(msg, "text", None) or "").strip()
        if not text:
            return

        chat_id = _chat_id_from_event(event)
        if chat_id is None or chat_id not in settings.chat_ids:
            return

        question = _strip_trigger(text, settings.trigger)
        if question is None:
            return
        if not question:
            await event.reply(
                "Research Memory (user MVP)\n"
                f"트리거 `{settings.trigger}` 뒤에 질문을 적어 주세요.\n"
                "예: 이번달 일정 / kmx 프로젝트에 대해 알려줘"
            )
            return

        logger.info(
            "telethon question chat_id=%s len=%s",
            chat_id,
            len(question),
        )
        status = await event.reply("Memory에서 확인하는 중…")
        try:
            chunks = await asyncio.to_thread(answer_question_chunks, memory, question)
        except Exception:
            logger.exception("telethon answer failed")
            await status.edit("Memory에 연결하지 못했습니다.")
            return

        if not chunks:
            await status.edit("(빈 답변)")
            return
        await status.edit(chunks[0])
        for extra in chunks[1:]:
            await event.reply(extra)

    logger.info(
        "starting telethon user MVP session=%s chats=%s trigger=%r",
        settings.session_path,
        sorted(settings.chat_ids),
        settings.trigger or "(all messages)",
    )
    async with client:
        # Interactive phone/code on first run; later uses session file.
        await client.start()
        me = await client.get_me()
        logger.info(
            "telethon logged in as id=%s username=%s",
            getattr(me, "id", None),
            getattr(me, "username", None),
        )
        print(
            "Telethon user MVP running. "
            f"chats={sorted(settings.chat_ids)} "
            f"trigger={settings.trigger or '(all)'} "
            "Ctrl+C to stop."
        )
        await client.run_until_disconnected()
    return 0
