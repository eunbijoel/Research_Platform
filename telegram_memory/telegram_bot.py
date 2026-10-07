"""Telegram handlers for Research Memory Bot (Bot API).

Auth: team roster group membership via getChatMember; answers only in private DMs.
Question → answer chunks go through user_router (shared with Telethon).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from auth import TelegramAuth, is_private_chat, status_allows_access
from user_router import answer_question_chunks

if TYPE_CHECKING:
    from app import AppContext

logger = logging.getLogger("research-memory-bot")

_REFUSAL_PREFIX = "메모리에 근거가 없어 답할 수 없습니다"
_ERROR_TEXT = "Memory에 연결하지 못했습니다."


def help_text(platform_url: str) -> str:
    """Start/help body. Platform URL first so web + bot stay discoverable together."""
    return (
        f"Research Memory Platform\n{platform_url}\n\n"
        "- /start, /help : 안내\n"
        "- /projects : 프로젝트 폴더 목록·개수\n"
        "- /latest : 최근 활동 폴더\n"
        "- /today · /week · /month : 일정\n"
        "- 일반 채팅 : Memory 문서에서 검색해 답합니다. 근거가 없으면 거절합니다.\n"
        "  (프로젝트·일정·공휴일·마지막 회의·첨부 회의록 질문은 자동으로 구조화 조회)\n"
        "- 사용: 팀 Telegram 방 멤버만 **봇과의 개인 DM**에서 질문합니다.\n"
        "  (팀 방은 자격 명단용이며, 방 안에서는 답하지 않습니다)\n"
        "- 저장·수정은 하지 않습니다 (읽기 전용)."
    )


class TelegramBotApp:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self.auth = TelegramAuth(
            member_chat_ids=ctx.settings.telegram_allowed_member_chat_ids,
        )

    def build(self) -> Application:
        app = Application.builder().token(self.ctx.settings.telegram_bot_token).build()
        app.add_handler(CommandHandler("start", self.on_start))
        app.add_handler(CommandHandler("help", self.on_help))
        app.add_handler(CommandHandler("projects", self.on_projects))
        app.add_handler(CommandHandler("latest", self.on_latest))
        app.add_handler(CommandHandler("today", self.on_today))
        app.add_handler(CommandHandler("week", self.on_week))
        app.add_handler(CommandHandler("month", self.on_month))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))
        return app

    async def on_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._gate(update, context):
            return
        if update.effective_message:
            await update.effective_message.reply_text(
                help_text(self.ctx.settings.platform_url)
            )

    async def on_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._gate(update, context):
            return
        if update.effective_message:
            await update.effective_message.reply_text(
                help_text(self.ctx.settings.platform_url)
            )

    async def on_projects(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._reply_via_router(
            update, context, "/projects", "프로젝트 목록을 불러오는 중…"
        )

    async def on_latest(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._reply_via_router(
            update, context, "/latest", "최근 활동 폴더를 불러오는 중…"
        )

    async def on_today(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._reply_via_router(update, context, "/today", "오늘 일정을 불러오는 중…")

    async def on_week(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._reply_via_router(
            update, context, "/week", "이번 주 일정을 불러오는 중…"
        )

    async def on_month(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._reply_via_router(
            update, context, "/month", "이번 달 일정을 불러오는 중…"
        )

    async def on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._gate(update, context):
            return
        message = update.effective_message
        if message is None:
            return
        question = (message.text or "").strip()
        if not question:
            return

        bot_username = (getattr(context.bot, "username", None) or "").strip()
        if bot_username and question.lower().startswith(f"@{bot_username.lower()}"):
            question = question[len(bot_username) + 1 :].strip()
        if not question:
            return

        await self._reply_via_router(
            update, context, question, "Memory에서 확인하는 중…", gated=True
        )

    async def _reply_via_router(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        question: str,
        waiting_label: str,
        *,
        gated: bool = False,
    ) -> None:
        if not gated and not await self._gate(update, context):
            return
        message = update.effective_message
        if message is None:
            return
        user_id = int(update.effective_user.id) if update.effective_user else 0
        chat_id = int(update.effective_chat.id) if update.effective_chat else 0
        status = await message.reply_text(waiting_label)
        try:
            chunks = await asyncio.to_thread(
                answer_question_chunks, self.ctx.memory, question
            )
        except Exception:
            logger.exception("router reply failed")
            await status.edit_text(_ERROR_TEXT)
            self._log_turn(
                user_id=user_id,
                chat_id=chat_id,
                question=question,
                answer=_ERROR_TEXT,
                refused=True,
                mode="error",
                citations=[],
            )
            return

        if not chunks:
            chunks = ["(빈 답변)"]
        await status.edit_text(chunks[0])
        for extra in chunks[1:]:
            await message.reply_text(extra)

        joined = "\n".join(chunks)
        refused = joined.startswith(_REFUSAL_PREFIX) or joined.startswith(_ERROR_TEXT)
        self._log_turn(
            user_id=user_id,
            chat_id=chat_id,
            question=question,
            answer=joined,
            refused=refused,
            mode="error" if joined.startswith(_ERROR_TEXT) else "router",
            citations=[],
        )

    def _log_turn(
        self,
        *,
        user_id: int,
        chat_id: int,
        question: str,
        answer: str,
        refused: bool,
        mode: str,
        citations: list,
    ) -> None:
        try:
            self.ctx.store.add_turn(
                telegram_user_id=user_id,
                telegram_chat_id=chat_id,
                question=question,
                answer=answer,
                refused=refused,
                mode=mode,
                citations=citations,
            )
        except Exception:
            logger.exception("chat log write failed")

    async def _user_in_roster(self, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
        """True if user is an active member of any roster chat (OR). Fail-closed."""
        for chat_id in self.auth.member_chat_ids:
            try:
                member = await context.bot.get_chat_member(chat_id, user_id)
            except Exception:
                logger.warning(
                    "get_chat_member failed chat_id=%s user_id=%s",
                    chat_id,
                    user_id,
                    exc_info=True,
                )
                continue
            status = getattr(member, "status", None)
            if status_allows_access(status):
                return True
        return False

    async def _gate(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        user = update.effective_user
        chat = update.effective_chat
        if user is None or chat is None:
            return False

        # Roster groups are membership lists only — never answer there.
        if not is_private_chat(chat):
            logger.info(
                "ignore non-private telegram chat_id=%s type=%s",
                getattr(chat, "id", None),
                getattr(chat, "type", None),
            )
            return False

        user_id = getattr(user, "id", None)
        if not isinstance(user_id, int):
            return False

        if await self._user_in_roster(context, user_id):
            return True

        logger.warning(
            "unauthorized telegram dm user_id=%s roster=%s",
            user_id,
            sorted(self.auth.member_chat_ids),
        )
        if update.effective_message:
            await update.effective_message.reply_text("사용할 수 없는 계정입니다.")
        return False
