import logging
from datetime import datetime

import requests as http_requests

from app.config import TELEGRAM_BOT_TOKEN

logger = logging.getLogger(__name__)

_TELEGRAM_MAX_CHARS = 4000


def send_telegram(chat_id: int, text: str, parse_mode: str | None = None) -> dict:
    """Send a Telegram message, splitting into chunks if it exceeds 4000 characters."""
    chunks = [text[i:i + _TELEGRAM_MAX_CHARS] for i in range(0, len(text), _TELEGRAM_MAX_CHARS)]
    for i, chunk in enumerate(chunks):
        payload: dict = {"chat_id": chat_id, "text": chunk}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        try:
            resp = http_requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json=payload,
                timeout=10,
            )
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("send_telegram | failed | chat_id=%s | chunk=%d/%d | error=%s", chat_id, i + 1, len(chunks), exc)
            return {"ok": False, "error": str(exc)}
    return {"ok": True}


def friendly_datetime(date_str: str, time_str: str) -> str:
    """Return a human-readable date/time string, e.g. 'Thursday, March 9 at 15:00'."""
    try:
        dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        return dt.strftime("%A, %B %-d at %H:%M")
    except ValueError:
        return f"{date_str} at {time_str}"
