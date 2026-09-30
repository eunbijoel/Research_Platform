"""Research Memory Bot entrypoint. Independent of Streamlit and Coding Agent."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from storage import DEFAULT_DB, ChatLogStore


ROOT = Path(__file__).resolve().parent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger("research-memory-bot")


DEFAULT_PLATFORM_URL = "http://bigsoft.iptime.org:51100/"


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_allowed_user_ids: frozenset[int]
    telegram_allowed_chat_ids: frozenset[int]
    chat_db_path: Path
    platform_url: str


class AppContext:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        from service import MemoryService

        self.memory = MemoryService()
        self.store = ChatLogStore(settings.chat_db_path)

    def close(self) -> None:
        self.store.close()


def _require(name: str, value: str | None) -> str:
    if value is None or not str(value).strip():
        raise SystemExit(
            f"missing required environment variable: {name}\n"
            "copy .env.example to .env and fill TELEGRAM_BOT_TOKEN / "
            "TELEGRAM_ALLOWED_USER_IDS / TELEGRAM_ALLOWED_CHAT_IDS "
            "(or singular TELEGRAM_ALLOWED_USER_ID / TELEGRAM_ALLOWED_CHAT_ID)"
        )
    return str(value).strip()


def _parse_id_set(*env_names: str) -> frozenset[int]:
    """Parse comma-separated int IDs from the first non-empty env among names."""
    values: set[int] = set()
    for name in env_names:
        raw = os.getenv(name, "")
        if raw is None or not str(raw).strip():
            continue
        for part in str(raw).split(","):
            piece = part.strip()
            if not piece:
                continue
            try:
                values.add(int(piece))
            except ValueError as exc:
                raise SystemExit(
                    f"invalid id in {name}: {piece!r} (expected integers, comma-separated)"
                ) from exc
    return frozenset(values)


def load_env_files() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(ROOT / ".env", override=False)


def load_settings() -> Settings:
    load_env_files()
    db_raw = os.getenv("TELEGRAM_CHAT_DB", "").strip()
    user_ids = _parse_id_set("TELEGRAM_ALLOWED_USER_IDS", "TELEGRAM_ALLOWED_USER_ID")
    chat_ids = _parse_id_set("TELEGRAM_ALLOWED_CHAT_IDS", "TELEGRAM_ALLOWED_CHAT_ID")
    if not user_ids:
        raise SystemExit(
            "missing TELEGRAM_ALLOWED_USER_IDS (or TELEGRAM_ALLOWED_USER_ID)\n"
            "copy .env.example to .env and set one or more numeric Telegram user ids"
        )
    if not chat_ids:
        raise SystemExit(
            "missing TELEGRAM_ALLOWED_CHAT_IDS (or TELEGRAM_ALLOWED_CHAT_ID)\n"
            "copy .env.example to .env and set private chat id(s) and/or group id(s)"
        )
    platform_url = (os.getenv("PLATFORM_URL") or DEFAULT_PLATFORM_URL).strip()
    if not platform_url:
        platform_url = DEFAULT_PLATFORM_URL
    return Settings(
        telegram_bot_token=_require("TELEGRAM_BOT_TOKEN", os.getenv("TELEGRAM_BOT_TOKEN")),
        telegram_allowed_user_ids=user_ids,
        telegram_allowed_chat_ids=chat_ids,
        chat_db_path=Path(db_raw) if db_raw else DEFAULT_DB,
        platform_url=platform_url.rstrip("/") + "/",
    )


def run_check() -> int:
    env_path = ROOT / ".env"
    example_path = ROOT / ".env.example"
    from service import memory_engine_status

    print("research-memory-bot")
    print(f"env_example={example_path}")
    print(f"env_file={'present' if env_path.exists() else 'missing (ok until token)'}")
    print(f"memory_engine={memory_engine_status()}")
    print(f"chat_db={DEFAULT_DB}")
    print("telegram_polling=not started (use python app.py after filling .env)")
    print("telethon_user=python app.py user  (needs TELETHON_API_ID/HASH/CHAT_IDS)")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "check":
        return run_check()
    if argv and argv[0] == "user":
        load_env_files()
        from telethon_user import run_telethon_user

        return asyncio.run(run_telethon_user())

    ctx = AppContext(load_settings())
    from telegram_bot import TelegramBotApp

    bot = TelegramBotApp(ctx)
    application = bot.build()
    logger.info(
        "starting telegram long polling allowed_users=%s allowed_chats=%s chat_db=%s",
        sorted(ctx.settings.telegram_allowed_user_ids),
        sorted(ctx.settings.telegram_allowed_chat_ids),
        ctx.settings.chat_db_path,
    )
    try:
        application.run_polling(allowed_updates=["message", "callback_query"])
    finally:
        ctx.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
