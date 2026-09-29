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


def _auth(
    users: frozenset[int] | set[int] | None = None,
    chats: frozenset[int] | set[int] | None = None,
) -> TelegramAuth:
    return TelegramAuth(
        allowed_user_ids=frozenset(users or {111}),
        allowed_chat_ids=frozenset(chats or {222}),
    )


def test_unauthorized_user_blocked() -> None:
    assert auth_ok(FakeUpdate(FakeUser(999), FakeChat(222)), _auth()) is False


def test_unauthorized_chat_blocked() -> None:
    assert auth_ok(FakeUpdate(FakeUser(111), FakeChat(999)), _auth()) is False


def test_authorized_user_allowed() -> None:
    assert auth_ok(FakeUpdate(FakeUser(111), FakeChat(222)), _auth()) is True


def test_missing_user_or_chat_blocked() -> None:
    assert auth_ok(FakeUpdate(None, FakeChat(222)), _auth()) is False
    assert auth_ok(FakeUpdate(FakeUser(111), None), _auth()) is False


def test_multiple_allowed_users_and_chats() -> None:
    auth = _auth(users={111, 222, 333}, chats={-100123, 111})
    assert auth_ok(FakeUpdate(FakeUser(222), FakeChat(-100123)), auth) is True
    assert auth_ok(FakeUpdate(FakeUser(333), FakeChat(111)), auth) is True
    assert auth_ok(FakeUpdate(FakeUser(999), FakeChat(-100123)), auth) is False
    assert auth_ok(FakeUpdate(FakeUser(222), FakeChat(999)), auth) is False
