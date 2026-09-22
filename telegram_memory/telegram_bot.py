"""Telegram handlers for Research Memory Bot (skeleton: /start and /help)."""

from __future__ import annotations

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

if TYPE_CHECKING:
    from app import AppContext

logger = logging.getLogger("research-memory-bot")

HELP_TEXT = """Research Memory Bot

이동 중에 연구자료·연구기록을 자연어로 묻고, 근거와 출처를 확인하는 봇입니다.

지금은 뼈대만 동작합니다.
- /start, /help : 이 안내
- 질문 검색은 다음 단계에서 Memory Engine에 연결합니다.

Streamlit 웹앱과 별도 프로세스이며, Coding Agent 봇과는 토큰·프로세스가 다릅니다.
"""

NOT_READY_TEXT = "질문 검색은 아직 연결되지 않았습니다. /help 를 참고하세요."


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
        if update.effective_message:
            await update.effective_message.reply_text(NOT_READY_TEXT)

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
