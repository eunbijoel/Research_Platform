"""Bot access helpers. Allowed-user checks use ChatLogStore (SQLite)."""

from __future__ import annotations


def is_private_chat(chat: object | None) -> bool:
    chat_type = getattr(chat, "type", None)
    return chat_type == "private"
