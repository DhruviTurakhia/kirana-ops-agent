from __future__ import annotations

import asyncio
import html
import logging
import re
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from kirana_agent.agent.runtime import StoreAgentRuntime
from kirana_agent.agent.tools import AgentContext
from kirana_agent.artifacts.deck import SalesDeckGenerator
from kirana_agent.artifacts.invoice import InvoiceGenerator
from kirana_agent.domain.service import StoreService

logger = logging.getLogger(__name__)

_COMMONMARK_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", re.DOTALL)


def _telegram_html(text: str) -> str:
    """Render the small CommonMark subset used by agent replies as safe Telegram HTML."""
    escaped = html.escape(text, quote=False)
    return _COMMONMARK_BOLD.sub(r"<b>\1</b>", escaped)


def _chunks(text: str, limit: int = 4000) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks = []
    remaining = text
    while remaining:
        split_at = remaining.rfind("\n", 0, limit)
        if split_at < limit // 2:
            split_at = remaining.rfind(" ", 0, limit)
        if split_at < limit // 2:
            split_at = limit
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    return chunks


class KiranaTelegramBot:
    def __init__(
        self,
        *,
        token: str,
        allow_all_users: bool = False,
        authorized_user_ids: Sequence[int],
        service: StoreService,
        runtime: StoreAgentRuntime,
        invoice_generator: InvoiceGenerator,
        deck_generator: SalesDeckGenerator,
    ):
        self.service = service
        self.runtime = runtime
        self.invoice_generator = invoice_generator
        self.deck_generator = deck_generator
        self.allow_all_users = allow_all_users
        self.authorized_user_ids = frozenset(authorized_user_ids)
        self._chat_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.application: Application = (
            ApplicationBuilder().token(token).concurrent_updates(16).build()
        )
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CommandHandler("new", self.new_chat))
        self.application.add_handler(CommandHandler("help", self.help_message))
        self.application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text)
        )
        self.application.add_error_handler(self.on_error)

    def run(self) -> None:
        self.application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=False,
        )

    def _authorized(self, update: Update) -> bool:
        user = update.effective_user
        return bool(
            user and (self.allow_all_users or user.id in self.authorized_user_ids)
        )

    async def _reject_unauthorized(self, update: Update) -> None:
        if update.effective_message:
            await update.effective_message.reply_text(
                "This bot is private. Ask the store owner to add your numeric Telegram user ID."
            )

    async def start(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            await self._reject_unauthorized(update)
            return
        assert update.effective_message is not None
        await update.effective_message.reply_text(
            "Kirana Ops Agent is ready. Tell me what happened in plain language — "
            "for example, receive stock, build a bill, check Khata, close the day, "
            "or request an invoice/deck. Use /new to clear chat context without "
            "clearing store memory."
        )

    async def help_message(
        self, update: Update, _context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._authorized(update):
            await self._reject_unauthorized(update)
            return
        assert update.effective_message is not None
        await update.effective_message.reply_text(
            "Just describe the shop task naturally. I'll ask one clarification if a product, "
            "customer, payment reference, or finalization is genuinely ambiguous."
        )

    async def new_chat(
        self, update: Update, _context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._authorized(update):
            await self._reject_unauthorized(update)
            return
        assert update.effective_chat is not None
        assert update.effective_user is not None
        assert update.effective_message is not None
        chat_id = str(update.effective_chat.id)
        source_event_id = str(update.update_id)
        async with self._chat_locks[chat_id]:
            claim = await asyncio.to_thread(
                self.service.claim_telegram_update,
                update_id=source_event_id,
                chat_id=chat_id,
                user_id=str(update.effective_user.id),
            )
            if not claim["claimed"]:
                if claim["status"] == "COMPLETED" and claim.get("response_text"):
                    await update.effective_message.reply_text(claim["response_text"])
                return
            result = await asyncio.to_thread(self.service.rotate_agent_session, chat_id)
            response = (
                "New chat started. Conversation context is clear; stock, bills, Khata, "
                "open drafts, and your standing preferences are still saved."
            )
            await asyncio.to_thread(
                self.service.complete_telegram_update,
                update_id=source_event_id,
                response_text=response,
                artifacts=[],
            )
            logger.info("Rotated Telegram agent session", extra={"session_id": result["session_id"]})
            await update.effective_message.reply_text(response)

    async def handle_text(
        self, update: Update, _telegram_context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._authorized(update):
            await self._reject_unauthorized(update)
            return
        message = update.effective_message
        user = update.effective_user
        chat = update.effective_chat
        if message is None or user is None or chat is None or not message.text:
            return
        chat_id = str(chat.id)
        source_event_id = str(update.update_id)
        async with self._chat_locks[chat_id]:
            claim = await asyncio.to_thread(
                self.service.claim_telegram_update,
                update_id=source_event_id,
                chat_id=chat_id,
                user_id=str(user.id),
            )
            if not claim["claimed"]:
                if claim["status"] == "COMPLETED":
                    await self._send_response(
                        update,
                        claim.get("response_text") or "Already completed.",
                        claim.get("artifacts", []),
                    )
                elif claim["status"] == "FAILED":
                    retry = await asyncio.to_thread(
                        self.service.retry_failed_telegram_update, source_event_id
                    )
                    if retry:
                        await self._process_claimed(update, message.text, chat_id, source_event_id)
                return
            await self._process_claimed(update, message.text, chat_id, source_event_id)

    async def _process_claimed(
        self, update: Update, text: str, chat_id: str, source_event_id: str
    ) -> None:
        assert update.effective_user is not None
        assert update.effective_chat is not None
        try:
            await update.effective_chat.send_action(ChatAction.TYPING)
            context = AgentContext(
                service=self.service,
                invoice_generator=self.invoice_generator,
                deck_generator=self.deck_generator,
                owner_id=str(update.effective_user.id),
                chat_id=chat_id,
                source_event_id=source_event_id,
            )
            session_id = await asyncio.to_thread(
                self.service.get_agent_session_id, chat_id
            )
            response, usage = await self.runtime.run_turn(
                message=text,
                context=context,
                session_id=session_id,
            )
            artifact_dicts = [artifact.as_dict() for artifact in context.artifacts]
            # Mark the durable result before network delivery. A replay can safely
            # resend this response while all business mutations remain exactly-once.
            await asyncio.to_thread(
                self.service.complete_telegram_update,
                update_id=source_event_id,
                response_text=response,
                artifacts=artifact_dicts,
            )
            await self._send_response(update, response, artifact_dicts)
            logger.info(
                "Completed agent turn",
                extra={
                    "chat_id": chat_id,
                    "source_event_id": source_event_id,
                    **usage,
                },
            )
        except Exception as error:
            logger.exception("Telegram turn failed")
            await asyncio.to_thread(
                self.service.fail_telegram_update,
                update_id=source_event_id,
                error_text=str(error),
            )
            if update.effective_message:
                await update.effective_message.reply_text(
                    "I couldn't complete that turn safely. No unconfirmed success should be assumed. "
                    "Please retry the same message; mutation tools are idempotent."
                )

    async def _send_response(
        self,
        update: Update,
        response: str,
        artifacts: Sequence[dict[str, Any]],
    ) -> None:
        assert update.effective_message is not None
        for chunk in _chunks(response):
            await update.effective_message.reply_text(
                _telegram_html(chunk),
                parse_mode=ParseMode.HTML,
            )
        for artifact in artifacts:
            path = Path(artifact["path"])
            if not path.is_file():
                logger.error("Stored artifact is missing", extra={"path": str(path)})
                continue
            with path.open("rb") as handle:
                await update.effective_message.reply_document(
                    document=handle,
                    filename=artifact["filename"],
                    caption=artifact["caption"],
                )

    async def on_error(
        self, update: object, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        logger.exception("Unhandled Telegram application error", exc_info=context.error)
