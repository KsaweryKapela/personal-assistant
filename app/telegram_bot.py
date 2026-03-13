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
    message_id = update.message.message_id
    from_user = update.message.from_user
    user_info = (
        f"user_id={from_user.id} username={from_user.username!r} name={from_user.full_name!r}"
        if from_user else "from_user=unknown"
    )
    text = update.message.text

    logger.info(
        "TEXT_MESSAGE_IN | chat_id=%s | message_id=%s | %s | text_len=%d",
        chat_id, message_id, user_info, len(text),
    )
    logger.info("TEXT_MESSAGE_IN | text: %s", text)

    t0 = time.monotonic()
    try:
        reply = await asyncio.to_thread(process_message, text, chat_id)
    except Exception as exc:
        logger.error(
            "TEXT_MESSAGE_ERROR | chat_id=%s | message_id=%s | error=%s",
            chat_id, message_id, exc, exc_info=True,
        )
        reply = "Something went wrong on my end. Please try again."

    await update.message.reply_text(reply)
    elapsed = time.monotonic() - t0
    logger.info(
        "TEXT_MESSAGE_OUT | chat_id=%s | message_id=%s | total_duration=%.2fs | reply_len=%d",
        chat_id, message_id, elapsed, len(reply),
    )
    logger.info("TEXT_MESSAGE_OUT | reply: %s", reply)


async def _handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Transcribe a voice message then process it like a text message."""
    if not update.message or not update.message.voice:
        return

    chat_id = update.message.chat_id
    message_id = update.message.message_id
    from_user = update.message.from_user
    user_info = (
        f"user_id={from_user.id} username={from_user.username!r} name={from_user.full_name!r}"
        if from_user else "from_user=unknown"
    )
    voice = update.message.voice

    logger.info(
        "VOICE_MESSAGE_IN | chat_id=%s | message_id=%s | %s | file_id=%s | duration=%ds | mime_type=%s | file_size=%s",
        chat_id, message_id, user_info,
        voice.file_id, voice.duration, voice.mime_type, voice.file_size,
    )

    t0 = time.monotonic()
    try:
        logger.info("VOICE_TRANSCRIBE_START | chat_id=%s | file_id=%s", chat_id, voice.file_id)
        text = await transcribe(voice.file_id, context.bot)
        transcribe_elapsed = time.monotonic() - t0
        logger.info(
            "VOICE_TRANSCRIBE_END | chat_id=%s | duration=%.2fs | text_len=%d",
            chat_id, transcribe_elapsed, len(text),
        )
        logger.info("VOICE_TRANSCRIBE_END | transcribed: %s", text)

        reply = await asyncio.to_thread(process_message, text, chat_id)
    except Exception as exc:
        logger.error(
            "VOICE_MESSAGE_ERROR | chat_id=%s | message_id=%s | error=%s",
            chat_id, message_id, exc, exc_info=True,
        )
        reply = "Sorry, I couldn't process that voice message."

    await update.message.reply_text(reply)
    elapsed = time.monotonic() - t0
    logger.info(
        "VOICE_MESSAGE_OUT | chat_id=%s | message_id=%s | total_duration=%.2fs | reply_len=%d",
        chat_id, message_id, elapsed, len(reply),
    )
    logger.info("VOICE_MESSAGE_OUT | reply: %s", reply)


def build_app() -> Application:
    logger.info("Building Telegram application")
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_message))
    app.add_handler(MessageHandler(filters.VOICE, _handle_voice))
    logger.info("Telegram application built | handlers=TEXT,VOICE")
    return app
