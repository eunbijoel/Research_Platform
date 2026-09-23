"""Telegram handlers for Research Memory Bot."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from auth import TelegramAuth, auth_ok
from format_reply import format_reply

if TYPE_CHECKING:
    from app import AppContext

logger = logging.getLogger("research-memory-bot")

HELP_TEXT = """Research Memory Bot

이동 중에 연구자료·연구기록을 자연어로 묻고, 근거와 출처를 확인하는 봇입니다.

- /start, /help : 이 안내
- 일반 채팅 : Memory에서 검색해 답합니다. 근거가 없으면 거절합니다.
- 저장·수정은 하지 않습니다 (읽기 전용).
"""


class TelegramBotApp:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self.auth = TelegramAuth(
            allowed_user_id=ctx.settings.telegram_allowed_user_id,
            allowed_chat_id=ctx.settings.telegram_allowed_chat_id,
        )

    def build(self) -> Application:
        app = Application.builder().token(self.ctx.settings.telegram_bot_token).build()
        app.add_handler(CommandHandler("start", self.on_start))
        app.add_handler(CommandHandler("help", self.on_help))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))
        return app

    async def on_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._gate(update):
            return
        if update.effective_message:
            await update.effective_message.reply_text(HELP_TEXT)

    async def on_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._gate(update):
            return
        if update.effective_message:
            await update.effective_message.reply_text(HELP_TEXT)

    async def on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._gate(update):
            return
        message = update.effective_message
        if message is None:
            return
        question = (message.text or "").strip()
        if not question:
            return
        status = await message.reply_text("Memory에서 근거를 찾는 중…")
        try:
            result = await asyncio.to_thread(self.ctx.memory.ask, question)
        except Exception:
            logger.exception("memory ask failed")
            await status.edit_text("Memory에 연결하지 못했습니다.")
            return
        chunks = format_reply(result, repo=self.ctx.memory.repo)
        await status.edit_text(chunks[0])
        for extra in chunks[1:]:
            await message.reply_text(extra)

    async def _gate(self, update: Update) -> bool:
        if auth_ok(update, self.auth):
            return True
        user = update.effective_user
        chat = update.effective_chat
        logger.warning(
            "unauthorized telegram request user_id=%s chat_id=%s",
            getattr(user, "id", None),
            getattr(chat, "id", None),
        )
        if update.callback_query:
            await update.callback_query.answer("권한이 없습니다.", show_alert=False)
        elif update.effective_message:
            await update.effective_message.reply_text("사용할 수 없는 계정입니다.")
        return False
