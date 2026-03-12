import logging

from telegram import Bot

from app.config import OPENAI_API_KEY

logger = logging.getLogger(__name__)


async def transcribe(file_id: str, bot: Bot) -> str:
    """Download a Telegram voice file and transcribe it with Whisper."""
    from openai import OpenAI

    tg_file = await bot.get_file(file_id)
    audio_bytes = await tg_file.download_as_bytearray()
    logger.info("Downloaded voice file %s (%d bytes)", file_id, len(audio_bytes))

    client = OpenAI(api_key=OPENAI_API_KEY)
    result = client.audio.transcriptions.create(
        model="whisper-1",
        file=("voice.ogg", bytes(audio_bytes), "audio/ogg"),
    )

    text = result.text.strip()
    logger.info("Transcribed voice: %r", text[:200])
    return text
