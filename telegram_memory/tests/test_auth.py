from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from auth import is_private_chat, status_allows_access


def test_status_allows_access() -> None:
    assert status_allows_access("creator") is True
    assert status_allows_access("administrator") is True
    assert status_allows_access("member") is True
    assert status_allows_access("restricted") is False
    assert status_allows_access("left") is False
    assert status_allows_access("kicked") is False
    assert status_allows_access(None) is False
    assert status_allows_access("") is False


def test_is_private_chat() -> None:
    assert is_private_chat(SimpleNamespace(type="private")) is True
    assert is_private_chat(SimpleNamespace(type="group")) is False
    assert is_private_chat(SimpleNamespace(type="supergroup")) is False
    assert is_private_chat(None) is False
