import logging

from app.openai_client import run_agent

logger = logging.getLogger(__name__)


def process_message(text: str) -> str:
    logger.info("Processing message: %r", text[:200])
    try:
        return run_agent(text)
    except Exception as exc:
        logger.error("Agent error: %s", exc)
        return "Sorry, something went wrong. Please try again."
