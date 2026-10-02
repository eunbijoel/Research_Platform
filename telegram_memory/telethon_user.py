"""Telethon/MTProto MVP: logged-in user account answers in allowlisted chats only.

Reuses user_router + MemoryService. No history scrape, no Memory writes.
Bot API (telegram_bot.py) is unchanged.
"""

from __future__ import annotations

import asyncio
import logging
import os
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
            "set one or more chat ids (comma-separated), e.g. your user id for Saved Messages "
            "or -100… for a group"
        )

    session_raw = (os.getenv("TELETHON_SESSION") or "").strip()
    session_path = Path(session_raw) if session_raw else DEFAULT_SESSION
    if not session_path.is_absolute():
        session_path = (ROOT / session_path).resolve()
    session_path.parent.mkdir(parents=True, exist_ok=True)

    return TelethonSettings(
        api_id=api_id,
        api_hash=api_hash,
        session_path=session_path,
        chat_ids=chat_ids,
    )


def _event_chat_id(event: Any) -> int | None:
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


def is_watched_incoming(event: Any, chat_ids: frozenset[int]) -> bool:
    """Incoming text in an allowlisted chat (skip our own outbound messages)."""
    if getattr(event, "out", False):
        return False
    chat_id = _event_chat_id(event)
    return chat_id is not None and chat_id in chat_ids


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
    if session.endswith(".session"):
        session_base = session[: -len(".session")]
    else:
        session_base = session

    client = TelegramClient(session_base, settings.api_id, settings.api_hash)
    chats = list(settings.chat_ids)

    @client.on(events.NewMessage(chats=chats, incoming=True))
    async def on_new_message(event: Any) -> None:  # noqa: ANN401
        if not is_watched_incoming(event, settings.chat_ids):
            return

        msg = event.message
        if msg is None:
            return
        question = (getattr(msg, "message", None) or getattr(msg, "text", None) or "").strip()
        if not question:
            return

        chat_id = _event_chat_id(event)
        logger.info("telethon question chat_id=%s len=%s", chat_id, len(question))
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
        "starting telethon chat MVP session=%s chats=%s",
        settings.session_path,
        sorted(settings.chat_ids),
    )
    async with client:
        await client.start()
        me = await client.get_me()
        logger.info(
            "telethon logged in as id=%s username=%s",
            getattr(me, "id", None),
            getattr(me, "username", None),
        )
        print(
            "Telethon chat MVP running. "
            f"chats={sorted(settings.chat_ids)} "
            "Ctrl+C to stop."
        )
        await client.run_until_disconnected()
    return 0
