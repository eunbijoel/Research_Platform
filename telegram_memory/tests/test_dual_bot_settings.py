from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import Settings, run_polling_apps  # noqa: E402


def test_bot_tokens_primary_only() -> None:
    settings = Settings(
        telegram_bot_token="111:AAA",
        telegram_bot_token_secondary="",
        chat_db_path=Path("x.sqlite3"),
        platform_url="http://example/",
    )
    assert settings.bot_tokens() == [("primary", "111:AAA")]


def test_bot_tokens_includes_secondary_when_set() -> None:
    settings = Settings(
        telegram_bot_token="111:AAA",
        telegram_bot_token_secondary="222:BBB",
        chat_db_path=Path("x.sqlite3"),
        platform_url="http://example/",
    )
    assert settings.bot_tokens() == [
        ("primary", "111:AAA"),
        ("secondary", "222:BBB"),
    ]


def _mock_app(*, fail_initialize: bool = False) -> MagicMock:
    app = MagicMock()
    if fail_initialize:
        app.initialize = AsyncMock(side_effect=RuntimeError("simulated start failure"))
    else:
        app.initialize = AsyncMock()
    app.start = AsyncMock()
    app.stop = AsyncMock()
    app.shutdown = AsyncMock()
    app.running = not fail_initialize
    if fail_initialize:
        app.updater = None
    else:
        app.updater = SimpleNamespace(
            start_polling=AsyncMock(),
            stop=AsyncMock(),
            running=True,
        )
    return app


def test_run_polling_apps_continues_when_one_start_fails() -> None:
    """One bot failing to start must not prevent the other from running."""
    bad = _mock_app(fail_initialize=True)
    good = _mock_app(fail_initialize=False)

    async def _run() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(
            run_polling_apps([("secondary", bad), ("primary", good)], stop_event=stop)
        )
        for _ in range(50):
            if good.updater.start_polling.await_count:
                break
            await asyncio.sleep(0.01)
        stop.set()
        await task

    asyncio.run(_run())
    good.initialize.assert_awaited()
    good.start.assert_awaited()
    good.updater.start_polling.assert_awaited()
    bad.initialize.assert_awaited()
    good.updater.stop.assert_awaited()


def test_run_polling_apps_exits_when_all_fail() -> None:
    bad = _mock_app(fail_initialize=True)
    with pytest.raises(SystemExit, match="no telegram bots started"):
        asyncio.run(run_polling_apps([("primary", bad)]))
