import asyncio
import logging
import time

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from app.assistant import process_message
from app.config import TELEGRAM_BOT_TOKEN

logger = logging.getLogger(__name__)


async def _handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle every incoming text message."""
    if not update.message or not update.message.text:
        return

    chat_id = update.message.chat_id
    text = update.message.text
    logger.info("Incoming message | chat_id=%s | %r", chat_id, text[:200])

    t0 = time.monotonic()
    try:
        reply = await asyncio.to_thread(process_message, text, chat_id)
    except Exception as exc:
        logger.error("Unhandled error processing message: %s", exc, exc_info=True)
        reply = "Something went wrong on my end. Please try again."

    await update.message.reply_text(reply)
    logger.info("Replied | chat_id=%s | %.2fs | %r", chat_id, time.monotonic() - t0, reply[:200])


def build_app() -> Application:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_message)
    )
    return app
