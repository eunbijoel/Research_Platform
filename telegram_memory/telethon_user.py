"""Telethon Research Assistant MVP (user account, 1:1 DM only).

Log in as a dedicated Assistant Telegram account. Team members DM that account;
allowlisted senders get answers via MemoryService / user_router; replies go back
in the same DM. No group listening, no history scrape, no Memory writes.
Bot API (telegram_bot.py) stays separate and unchanged.
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
    allowed_user_ids: frozenset[int]


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

    allowed = _parse_id_set("TELETHON_ALLOWED_USER_IDS")
    if not allowed:
        raise SystemExit(
            "missing TELETHON_ALLOWED_USER_IDS\n"
            "set team member Telegram user ids (comma-separated) who may DM the Assistant"
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
        allowed_user_ids=allowed,
    )


def _sender_id(event: Any) -> int | None:
    sid = getattr(event, "sender_id", None)
    if isinstance(sid, int):
        return sid
    sender = getattr(event, "sender", None)
    if sender is not None and hasattr(sender, "id"):
        try:
            return int(sender.id)
        except (TypeError, ValueError):
            return None
    return None


def is_private_incoming_dm(event: Any) -> bool:
    """True for an incoming private User chat (not groups/channels, not our own outs)."""
    if getattr(event, "out", False):
        return False
    # Telethon: is_private on NewMessage.Event
    is_private = getattr(event, "is_private", None)
    if is_private is False:
        return False
    if is_private is True:
        return True
    # Fallback: positive chat_id usually means user DM (Telethon peer id)
    chat_id = getattr(event, "chat_id", None)
    return isinstance(chat_id, int) and chat_id > 0


def is_allowed_sender(sender_id: int | None, allowed: frozenset[int]) -> bool:
    return sender_id is not None and sender_id in allowed


async def run_telethon_user() -> int:
    try:
        from telethon import TelegramClient, events
        from telethon.tl.types import User
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

    @client.on(events.NewMessage(incoming=True))
    async def on_new_message(event: Any) -> None:  # noqa: ANN401
        if not is_private_incoming_dm(event):
            return

        # Extra guard: chat entity should be a User (1:1), not Chat/Channel.
        try:
            chat = await event.get_chat()
        except Exception:
            logger.exception("telethon get_chat failed")
            return
        if not isinstance(chat, User):
            return

        sender_id = _sender_id(event)
        if not is_allowed_sender(sender_id, settings.allowed_user_ids):
            logger.warning("telethon unauthorized dm sender_id=%s", sender_id)
            try:
                await event.reply("사용할 수 없는 계정입니다.")
            except Exception:
                logger.exception("telethon unauthorized reply failed")
            return

        msg = event.message
        if msg is None:
            return
        question = (getattr(msg, "message", None) or getattr(msg, "text", None) or "").strip()
        if not question:
            return

        logger.info("telethon dm sender_id=%s len=%s", sender_id, len(question))
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
        "starting telethon Assistant DM MVP session=%s allowed_users=%s",
        settings.session_path,
        sorted(settings.allowed_user_ids),
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
            "Research Assistant (Telethon DM) running. "
            f"allowed_users={sorted(settings.allowed_user_ids)} "
            "Ctrl+C to stop."
        )
        await client.run_until_disconnected()
    return 0
