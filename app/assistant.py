import logging
import time
import uuid

from app.openai_client import run_agent

logger = logging.getLogger(__name__)


def process_message(text: str, chat_id: int = 0, message_type: str = "text") -> str:
    from app.database import save_message

    request_id = uuid.uuid4().hex[:8]
    logger.info(
        "[req=%s] Message received | chat_id=%s | type=%s | message_len=%d",
        request_id, chat_id, message_type, len(text),
    )
    logger.info("[req=%s] Message text: %s", request_id, text)

    save_message(chat_id, "user", text, message_type)

    t0 = time.monotonic()
    try:
        reply = run_agent(text, chat_id=chat_id, request_id=request_id)
        elapsed = time.monotonic() - t0
        logger.info(
            "[req=%s] Message processed | chat_id=%s | duration=%.2fs | reply_len=%d",
            request_id, chat_id, elapsed, len(reply),
        )
        save_message(chat_id, "assistant", reply)
        return reply
    except Exception as exc:
        elapsed = time.monotonic() - t0
        logger.error(
            "[req=%s] Agent error | chat_id=%s | duration=%.2fs | error_type=%s | error=%s",
            request_id, chat_id, elapsed, type(exc).__name__, exc, exc_info=True,
        )
        return "Sorry, something went wrong. Please try again."
