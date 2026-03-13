import asyncio
import logging
import time

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from app.assistant import process_message
from app.config import TELEGRAM_BOT_TOKEN
from app.voice import transcribe

logger = logging.getLogger(__name__)


async def _handle_message(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    chat_id = update.message.chat_id
    text = update.message.text
    logger.info("MSG IN | chat_id=%s | %r", chat_id, text[:300])

    t0 = time.monotonic()
    try:
        reply = await asyncio.to_thread(process_message, text, chat_id)
    except Exception as exc:
        logger.error("MSG ERROR | chat_id=%s | %s", chat_id, exc, exc_info=True)
        reply = "Something went wrong on my end. Please try again."

    await update.message.reply_text(reply)
    logger.info("MSG OUT | chat_id=%s | %.2fs | %r", chat_id, time.monotonic() - t0, reply[:300])


async def _handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.voice:
        return

    chat_id = update.message.chat_id
    voice = update.message.voice
    logger.info("VOICE IN | chat_id=%s | duration=%ds", chat_id, voice.duration)

    t0 = time.monotonic()
    try:
        text = await transcribe(voice.file_id, context.bot)
        logger.info("VOICE transcribed | %r", text[:300])
        await update.message.reply_text(f"🎤 _{text}_", parse_mode="Markdown")
        reply = await asyncio.to_thread(process_message, text, chat_id, "voice")
    except Exception as exc:
        logger.error("VOICE ERROR | chat_id=%s | %s", chat_id, exc, exc_info=True)
        reply = "Sorry, I couldn't process that voice message."

    await update.message.reply_text(reply)
    logger.info("VOICE OUT | chat_id=%s | %.2fs | %r", chat_id, time.monotonic() - t0, reply[:300])


def build_app() -> Application:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_message))
    app.add_handler(MessageHandler(filters.VOICE, _handle_voice))
    return app
