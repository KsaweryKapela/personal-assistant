"""
Telegram log handler — streams app logs to a dedicated Telegram bot.

Attach to the 'app' logger so all app.* modules are covered.
Uses a background thread + queue so logging never blocks the main flow.

Required env vars (optional — handler is skipped if absent):
    LOG_BOT_TOKEN  — token of the dedicated logging bot
    LOG_CHAT_ID    — your Telegram chat ID to receive logs
"""

import logging
import queue
import threading

import requests as http_requests

_LEVEL_PREFIX = {
    logging.DEBUG: "🔍 DEBUG",
    logging.INFO: "ℹ️ INFO",
    logging.WARNING: "⚠️ WARNING",
    logging.ERROR: "❌ ERROR",
    logging.CRITICAL: "🔥 CRITICAL",
}
_TELEGRAM_MAX = 4096


class TelegramLogHandler(logging.Handler):
    def __init__(self, token: str, chat_id: str | int, level: int = logging.INFO):
        super().__init__(level)
        self._token = token
        self._chat_id = chat_id
        self._queue: queue.SimpleQueue = queue.SimpleQueue()
        self._thread = threading.Thread(target=self._worker, daemon=True, name="log-bot")
        self._thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._queue.put_nowait(self.format(record))
        except Exception:
            pass  # Never raise from a log handler

    def _worker(self) -> None:
        while True:
            text = self._queue.get()
            chunks = [text[i:i + _TELEGRAM_MAX] for i in range(0, len(text), _TELEGRAM_MAX)]
            for chunk in chunks:
                try:
                    http_requests.post(
                        f"https://api.telegram.org/bot{self._token}/sendMessage",
                        json={"chat_id": self._chat_id, "text": chunk},
                        timeout=10,
                    )
                except Exception:
                    pass  # Silently drop — we must not recurse into logging


def _format(record: logging.LogRecord) -> str:
    import traceback
    prefix = _LEVEL_PREFIX.get(record.levelno, "📋 LOG")
    lines = [f"{prefix}  {record.name}", record.getMessage()]
    if record.exc_info:
        tb = "".join(traceback.format_exception(*record.exc_info)).strip()
        lines.append(tb[:2000])
    return "\n".join(lines)


def setup(token: str, chat_id: str | int) -> None:
    """Attach a TelegramLogHandler to the 'app' package logger."""
    handler = TelegramLogHandler(token, chat_id)
    handler.setFormatter(logging.Formatter())  # We do formatting ourselves
    handler.format = _format  # type: ignore[method-assign]
    logging.getLogger("app").addHandler(handler)
    logging.getLogger("app").info("Telegram log handler active.")
