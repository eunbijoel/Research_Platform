from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from auth import TelegramAuth, auth_ok


@dataclass
class FakeUser:
    id: int


@dataclass
class FakeChat:
    id: int


@dataclass
class FakeUpdate:
    effective_user: FakeUser | None
    effective_chat: FakeChat | None


def _auth(user: int = 111, chat: int = 222) -> TelegramAuth:
    return TelegramAuth(allowed_user_id=user, allowed_chat_id=chat)


def test_unauthorized_user_blocked() -> None:
    assert auth_ok(FakeUpdate(FakeUser(999), FakeChat(222)), _auth()) is False


def test_unauthorized_chat_blocked() -> None:
    assert auth_ok(FakeUpdate(FakeUser(111), FakeChat(999)), _auth()) is False


def test_authorized_user_allowed() -> None:
    assert auth_ok(FakeUpdate(FakeUser(111), FakeChat(222)), _auth()) is True


def test_missing_user_or_chat_blocked() -> None:
    assert auth_ok(FakeUpdate(None, FakeChat(222)), _auth()) is False
    assert auth_ok(FakeUpdate(FakeUser(111), None), _auth()) is False
