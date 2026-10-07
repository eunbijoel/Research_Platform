"""Bot access helpers. Membership checks that need Bot API live in telegram_bot."""

from __future__ import annotations

from dataclasses import dataclass

# Telegram ChatMember.status values that grant Research Bot DM access.
ALLOWED_MEMBER_STATUSES = frozenset({"creator", "administrator", "member"})


@dataclass(frozen=True)
class TelegramAuth:
    """Roster groups whose members may use the bot via private DM."""

    member_chat_ids: frozenset[int]


def status_allows_access(status: str | None) -> bool:
    """True only for active roster membership (fail-closed otherwise)."""
    if not status:
        return False
    return str(status).lower() in ALLOWED_MEMBER_STATUSES


def is_private_chat(chat: object | None) -> bool:
    chat_type = getattr(chat, "type", None)
    return chat_type == "private"
