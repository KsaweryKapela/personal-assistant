"""
Entry point — starts the Telegram bot.

Webhook mode (production):  set WEBHOOK_URL env var, e.g. https://my-app.railway.app
Polling mode  (local dev):  leave WEBHOOK_URL unset

Run with:
    python -m app.main
or:
    uv run python -m app.main
"""

import logging

from telegram import Update

from app.config import LOG_BOT_TOKEN, LOG_CHAT_ID, PORT, WEBHOOK_URL
from app.scheduler import start as start_scheduler
from app.telegram_bot import build_app

logging.basicConfig(
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    level=logging.INFO,
)
# Silence noisy third-party loggers
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("googleapiclient.discovery").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


def main() -> None:
    if LOG_BOT_TOKEN and LOG_CHAT_ID:
        from app.log_bot import setup as setup_log_bot
        setup_log_bot(LOG_BOT_TOKEN, LOG_CHAT_ID)
        logger.info("Telegram log bot enabled (chat_id=%s).", LOG_CHAT_ID)
    else:
        logger.info("LOG_BOT_TOKEN / LOG_CHAT_ID not set — Telegram logging disabled.")

    start_scheduler()
    app = build_app()

    if WEBHOOK_URL:
        logger.info("Starting bot in webhook mode (url=%s, port=%d)...", WEBHOOK_URL, PORT)
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            webhook_url=WEBHOOK_URL,
            allowed_updates=Update.ALL_TYPES,
        )
    else:
        logger.info("Starting bot in polling mode (local dev)...")
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
