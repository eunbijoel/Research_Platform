"""Research Memory Bot entrypoint. Independent of Streamlit and Coding Agent."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from dataclasses import dataclass
from pathlib import Path

from storage import DEFAULT_DB, ChatLogStore
from telegram.ext import Application


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
    telegram_bot_token_secondary: str
    chat_db_path: Path
    platform_url: str

    def bot_tokens(self) -> list[tuple[str, str]]:
        """Ordered (label, token) pairs. Secondary omitted when empty."""
        tokens: list[tuple[str, str]] = [("primary", self.telegram_bot_token)]
        secondary = (self.telegram_bot_token_secondary or "").strip()
        if secondary:
            tokens.append(("secondary", secondary))
        return tokens


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
            "copy .env.example to .env and fill TELEGRAM_BOT_TOKEN\n"
            "(allowed users are managed in the Streamlit Telegram admin page)"
        )
    return str(value).strip()


def _parse_id_set(*env_names: str) -> frozenset[int]:
    """Parse comma-separated int IDs from each listed env var (union)."""
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
    platform_url = (os.getenv("PLATFORM_URL") or DEFAULT_PLATFORM_URL).strip()
    if not platform_url:
        platform_url = DEFAULT_PLATFORM_URL
    secondary = (os.getenv("TELEGRAM_BOT_TOKEN_SECONDARY") or "").strip()
    return Settings(
        telegram_bot_token=_require("TELEGRAM_BOT_TOKEN", os.getenv("TELEGRAM_BOT_TOKEN")),
        telegram_bot_token_secondary=secondary,
        chat_db_path=Path(db_raw) if db_raw else DEFAULT_DB,
        platform_url=platform_url.rstrip("/") + "/",
    )


async def run_polling_apps(
    labeled_apps: list[tuple[str, Application]],
    *,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Run one or more PTB applications on a single asyncio loop (no nested run_polling)."""
    if not labeled_apps:
        raise SystemExit("no telegram applications to start")

    stop_event = stop_event or asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_stop(*_args: object) -> None:
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            # Windows / limited environments: rely on KeyboardInterrupt around asyncio.run
            pass

    started: list[tuple[str, Application]] = []
    try:
        for label, app in labeled_apps:
            try:
                await app.initialize()
                await app.start()
                if app.updater is None:
                    raise RuntimeError("application updater is not available")
                await app.updater.start_polling(allowed_updates=["message"])
                started.append((label, app))
                logger.info("telegram polling started bot=%s", label)
            except Exception:
                logger.exception(
                    "failed to start telegram bot=%s; other bots keep running",
                    label,
                )
                try:
                    if app.updater and app.updater.running:
                        await app.updater.stop()
                except Exception:
                    logger.exception("updater stop failed during start recovery bot=%s", label)
                try:
                    if app.running:
                        await app.stop()
                except Exception:
                    logger.exception("app stop failed during start recovery bot=%s", label)
                try:
                    await app.shutdown()
                except Exception:
                    logger.exception("app shutdown failed during start recovery bot=%s", label)

        if not started:
            raise SystemExit("no telegram bots started successfully")

        await stop_event.wait()
    finally:
        for label, app in reversed(started):
            try:
                if app.updater and app.updater.running:
                    await app.updater.stop()
            except Exception:
                logger.exception("updater stop failed on shutdown bot=%s", label)
            try:
                if app.running:
                    await app.stop()
            except Exception:
                logger.exception("app stop failed on shutdown bot=%s", label)
            try:
                await app.shutdown()
            except Exception:
                logger.exception("app shutdown failed on shutdown bot=%s", label)


def run_check() -> int:
    env_path = ROOT / ".env"
    example_path = ROOT / ".env.example"
    from service import memory_engine_status

    load_env_files()
    primary = bool((os.getenv("TELEGRAM_BOT_TOKEN") or "").strip())
    secondary = bool((os.getenv("TELEGRAM_BOT_TOKEN_SECONDARY") or "").strip())
    print("research-memory-bot")
    print(f"env_example={example_path}")
    print(f"env_file={'present' if env_path.exists() else 'missing (ok until token)'}")
    print(f"memory_engine={memory_engine_status()}")
    print(f"chat_db={DEFAULT_DB}")
    print(f"telegram_token_primary={'set' if primary else 'missing'}")
    print(f"telegram_token_secondary={'set' if secondary else 'optional/unset'}")
    print("telegram_polling=not started (use python app.py after filling .env)")
    print("telethon_user=python app.py user  (TELETHON_API_ID/HASH + TELETHON_CHAT_IDS)")
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

    token_pairs = ctx.settings.bot_tokens()
    labeled_apps: list[tuple[str, Application]] = [
        (label, TelegramBotApp(ctx, bot_label=label).build(token=token))
        for label, token in token_pairs
    ]

    logger.info(
        "starting telegram long polling chat_db=%s allowed_users=%s bots=%s",
        ctx.settings.chat_db_path,
        len(ctx.store.list_allowed_users()),
        ",".join(label for label, _ in token_pairs),
    )
    try:
        asyncio.run(run_polling_apps(labeled_apps))
    finally:
        ctx.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
