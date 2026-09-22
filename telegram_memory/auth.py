"""Allowlist for Research Memory Bot. No Telegram SDK import."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TelegramAuth:
    allowed_user_id: int
    allowed_chat_id: int


def auth_ok(update: Any, auth: TelegramAuth) -> bool:
    user = getattr(update, "effective_user", None)
    chat = getattr(update, "effective_chat", None)
    if user is None or chat is None:
        return False
    user_id = getattr(user, "id", None)
    chat_id = getattr(chat, "id", None)
    return user_id == auth.allowed_user_id and chat_id == auth.allowed_chat_id
