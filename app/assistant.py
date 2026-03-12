import logging
import time

from app.openai_client import run_agent

logger = logging.getLogger(__name__)


def process_message(text: str, chat_id: int = 0) -> str:
    logger.info("Processing message | chat_id=%s | %r", chat_id, text[:200])
    t0 = time.monotonic()
    try:
        reply = run_agent(text, chat_id=chat_id)
        elapsed = time.monotonic() - t0
        logger.info("Message processed in %.2fs | chat_id=%s", elapsed, chat_id)
        return reply
    except Exception as exc:
        elapsed = time.monotonic() - t0
        logger.error(
            "Agent error after %.2fs [%s]: %s",
            elapsed, type(exc).__name__, exc, exc_info=True,
        )
        return "Sorry, something went wrong. Please try again."
