import logging

from app.openai_client import run_agent

logger = logging.getLogger(__name__)


def process_message(text: str, chat_id: int = 0) -> str:
    logger.info("Processing message: %r", text[:200])
    try:
        return run_agent(text, chat_id=chat_id)
    except Exception as exc:
        logger.error("Agent error [%s]: %s", type(exc).__name__, exc, exc_info=True)
        return "Sorry, something went wrong. Please try again."
