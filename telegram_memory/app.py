"""Research Memory Bot entrypoint. Independent of Streamlit and Coding Agent."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


def load_env_files() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(ROOT / ".env", override=False)


def load_settings(*, require_token: bool = True) -> Settings:
    load_env_files()
    db_raw = os.getenv("TELEGRAM_CHAT_DB", "").strip()
    platform_url = (os.getenv("PLATFORM_URL") or DEFAULT_PLATFORM_URL).strip()
    if not platform_url:
        platform_url = DEFAULT_PLATFORM_URL
    secondary = (os.getenv("TELEGRAM_BOT_TOKEN_SECONDARY") or "").strip()
    raw_token = os.getenv("TELEGRAM_BOT_TOKEN")
    token = _require("TELEGRAM_BOT_TOKEN", raw_token) if require_token else (raw_token or "").strip()
    return Settings(
        telegram_bot_token=token,
        telegram_bot_token_secondary=secondary,
        chat_db_path=Path(db_raw) if db_raw else DEFAULT_DB,
        platform_url=platform_url.rstrip("/") + "/",
    )


async def run_polling_apps(
    labeled_apps: list[tuple[str, Application]],
    *,
    stop_event: asyncio.Event | None = None,
    notifier: Any | None = None,
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
    notify_task: asyncio.Task[None] | None = None
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

        primary = next((app for label, app in started if label == "primary"), None)
        if notifier is not None and primary is not None:
            notify_task = asyncio.create_task(notifier.serve(primary.bot, stop_event))
        elif notifier is not None:
            logger.warning("schedule notifier skipped: primary bot did not start")

        await stop_event.wait()
    finally:
        if notify_task is not None:
            notify_task.cancel()
            try:
                await notify_task
            except asyncio.CancelledError:
                pass
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
    print("schedule_notifier=daily 09:00 Asia/Seoul on the primary bot when polling")
    print("notify_dry_run=python app.py notify --dry-run")
    return 0


def run_notify(argv: list[str]) -> int:
    """Send today's notices, or print them with --dry-run. Primary token only."""
    import argparse
    from datetime import date

    from schedule_notifier import build_plans, dispatch_plans, kst_today

    parser = argparse.ArgumentParser(prog="app.py notify")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print notices without sending or recording them",
    )
    parser.add_argument(
        "--only-user",
        type=int,
        default=None,
        help="send only to this allowlisted user id (must have a private DM)",
    )
    parser.add_argument(
        "--today",
        default="",
        help="treat this YYYY-MM-DD as the Korea calendar day",
    )
    args = parser.parse_args(argv)
    if args.today:
        today = date.fromisoformat(args.today)
    else:
        today = kst_today()

    ctx = AppContext(load_settings(require_token=not args.dry_run))
    try:
        plans, reason = build_plans(
            ctx.memory.repo,
            ctx.store,
            today=today,
            platform_url=ctx.settings.platform_url,
            only_user=args.only_user,
        )
        if reason:
            print(reason)
        if args.dry_run:
            for plan in plans:
                print("---")
                print(
                    f"user={plan.telegram_user_id} kind={plan.kind} "
                    f"schedule={plan.schedule_id} anchor={plan.anchor_date}"
                )
                print(plan.text)
            print(f"dry-run notices={len(plans)} today={today.isoformat()}")
            return 0

        from telegram import Bot

        async def _send_all() -> list:
            bot = Bot(ctx.settings.telegram_bot_token)
            await bot.initialize()

            async def _send(user_id: int, text: str) -> None:
                await bot.send_message(
                    chat_id=user_id,
                    text=text,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )

            try:
                return await dispatch_plans(
                    plans,
                    ctx.store,
                    _send,
                    owner=f"cli-{os.getpid()}",
                )
            finally:
                await bot.shutdown()

        failed = asyncio.run(_send_all())
        print(
            f"notices={len(plans)} failed={len(failed)} today={today.isoformat()} "
            f"only_user={args.only_user if args.only_user is not None else 'allowlist'}"
        )
        return 1 if failed else 0
    finally:
        ctx.close()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "check":
        return run_check()
    if argv and argv[0] == "notify":
        return run_notify(argv[1:])

    ctx = AppContext(load_settings())
    from schedule_notifier import ScheduleNotifier
    from telegram_bot import TelegramBotApp

    token_pairs = ctx.settings.bot_tokens()
    labeled_apps: list[tuple[str, Application]] = [
        (label, TelegramBotApp(ctx, bot_label=label).build(token=token))
        for label, token in token_pairs
    ]
    notifier = ScheduleNotifier(
        repo=ctx.memory.repo,
        store=ctx.store,
        platform_url=ctx.settings.platform_url,
    )

    logger.info(
        "starting telegram long polling chat_db=%s allowed_users=%s bots=%s",
        ctx.settings.chat_db_path,
        len(ctx.store.list_allowed_users()),
        ",".join(label for label, _ in token_pairs),
    )
    try:
        asyncio.run(run_polling_apps(labeled_apps, notifier=notifier))
    finally:
        ctx.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
