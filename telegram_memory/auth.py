"""Allowlist for Research Memory Bot. No Telegram SDK import."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TelegramAuth:
    """Allow when user_id ∈ allowed_user_ids AND chat_id ∈ allowed_chat_ids."""

    allowed_user_ids: frozenset[int]
    allowed_chat_ids: frozenset[int]


def auth_ok(update: Any, auth: TelegramAuth) -> bool:
    user = getattr(update, "effective_user", None)
    chat = getattr(update, "effective_chat", None)
    if user is None or chat is None:
        return False
    user_id = getattr(user, "id", None)
    chat_id = getattr(chat, "id", None)
    if not isinstance(user_id, int) or not isinstance(chat_id, int):
        return False
    return user_id in auth.allowed_user_ids and chat_id in auth.allowed_chat_ids
