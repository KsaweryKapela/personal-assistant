"""
Entry point — starts the Telegram bot in long-polling mode.

Run with:
    python -m app.main
or:
    uv run python -m app.main
"""

import logging

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
    logger.info("Starting personal assistant bot (polling mode)...")
    app = build_app()
    app.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
