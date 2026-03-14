import logging
import time

from telegram import Bot

from app.config import OPENAI_API_KEY

logger = logging.getLogger(__name__)

_WHISPER_MODEL = "whisper-1"


async def transcribe(file_id: str, bot: Bot) -> str:
    """Download a Telegram voice file and transcribe it with Whisper."""
    from openai import OpenAI

    # Download phase
    logger.info("Voice download | start | file_id=%s", file_id)
    t_download = time.monotonic()
    tg_file = await bot.get_file(file_id)
    audio_bytes = await tg_file.download_as_bytearray()
    download_elapsed = time.monotonic() - t_download
    logger.info(
        "Voice download | ok | file_id=%s | size=%d bytes | duration=%.2fs",
        file_id, len(audio_bytes), download_elapsed,
    )

    # Transcription phase
    logger.info(
        "Voice transcription | start | model=%s | file_id=%s | audio_size=%d bytes",
        _WHISPER_MODEL, file_id, len(audio_bytes),
    )
    t_transcribe = time.monotonic()
    client = OpenAI(api_key=OPENAI_API_KEY)
    result = client.audio.transcriptions.create(
        model=_WHISPER_MODEL,
        file=("voice.ogg", bytes(audio_bytes), "audio/ogg"),
        language="en",
    )
    transcribe_elapsed = time.monotonic() - t_transcribe

    text = result.text.strip()
    logger.info(
        "Voice transcription | ok | model=%s | duration=%.2fs | text_len=%d",
        _WHISPER_MODEL, transcribe_elapsed, len(text),
    )
    logger.info("Voice transcription | text: %s", text)
    return text
