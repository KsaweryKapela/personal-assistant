import asyncio
import logging
import time

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from app.assistant import process_message
from app.config import TELEGRAM_BOT_TOKEN
from app.voice import transcribe

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


async def _handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Transcribe a voice message then process it like a text message."""
    if not update.message or not update.message.voice:
        return

    chat_id = update.message.chat_id
    logger.info("Incoming voice | chat_id=%s | duration=%ds", chat_id, update.message.voice.duration)

    t0 = time.monotonic()
    try:
        text = await transcribe(update.message.voice.file_id, context.bot)
        logger.info("Voice transcribed in %.2fs | %r", time.monotonic() - t0, text[:200])
        reply = await asyncio.to_thread(process_message, text, chat_id)
    except Exception as exc:
        logger.error("Error processing voice message: %s", exc, exc_info=True)
        reply = "Sorry, I couldn't process that voice message."

    await update.message.reply_text(reply)
    logger.info("Replied | chat_id=%s | %.2fs | %r", chat_id, time.monotonic() - t0, reply[:200])


def build_app() -> Application:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_message))
    app.add_handler(MessageHandler(filters.VOICE, _handle_voice))
    return app
