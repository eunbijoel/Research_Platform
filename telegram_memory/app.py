"""Research Memory Bot entrypoint. Independent of Streamlit and Coding Agent."""

from __future__ import annotations

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


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_allowed_user_id: int
    telegram_allowed_chat_id: int
    chat_db_path: Path


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
            "TELEGRAM_ALLOWED_USER_ID / TELEGRAM_ALLOWED_CHAT_ID"
        )
    return str(value).strip()


def load_env_files() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(ROOT / ".env", override=False)


def load_settings() -> Settings:
    load_env_files()
    db_raw = os.getenv("TELEGRAM_CHAT_DB", "").strip()
    return Settings(
        telegram_bot_token=_require("TELEGRAM_BOT_TOKEN", os.getenv("TELEGRAM_BOT_TOKEN")),
        telegram_allowed_user_id=int(
            _require("TELEGRAM_ALLOWED_USER_ID", os.getenv("TELEGRAM_ALLOWED_USER_ID"))
        ),
        telegram_allowed_chat_id=int(
            _require("TELEGRAM_ALLOWED_CHAT_ID", os.getenv("TELEGRAM_ALLOWED_CHAT_ID"))
        ),
        chat_db_path=Path(db_raw) if db_raw else DEFAULT_DB,
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
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "check":
        return run_check()

    ctx = AppContext(load_settings())
    from telegram_bot import TelegramBotApp
    bot = TelegramBotApp(ctx)
    application = bot.build()
    logger.info(
        "starting telegram long polling allowed_user=%s allowed_chat=%s chat_db=%s",
        ctx.settings.telegram_allowed_user_id,
        ctx.settings.telegram_allowed_chat_id,
        ctx.settings.chat_db_path,
    )
    try:
        application.run_polling(allowed_updates=["message", "callback_query"])
    finally:
        ctx.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
