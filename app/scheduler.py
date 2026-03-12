"""
Background scheduler — sends proactive Telegram messages at scheduled times.

Jobs are persisted to scheduler.json so they survive restarts.
The background thread wakes every 60 s, sends due messages, and removes them.
"""

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone

import requests as http_requests

from app.config import TELEGRAM_BOT_TOKEN

logger = logging.getLogger(__name__)

_SCHEDULER_FILE = "scheduler.json"
_jobs: list[dict] = []
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _load_jobs() -> list[dict]:
    if os.path.exists(_SCHEDULER_FILE):
        try:
            with open(_SCHEDULER_FILE) as f:
                return json.load(f)
        except Exception as exc:
            logger.warning("Could not load %s: %s", _SCHEDULER_FILE, exc)
    return []


def _save_jobs() -> None:
    """Must be called while holding _lock."""
    try:
        with open(_SCHEDULER_FILE, "w") as f:
            json.dump(_jobs, f, indent=2)
    except Exception as exc:
        logger.warning("Could not save %s: %s", _SCHEDULER_FILE, exc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def add_job(chat_id: int, message: str, send_at: datetime, context: str = "") -> dict:
    """Add a new scheduled message job."""
    job = {
        "id": str(uuid.uuid4()),
        "chat_id": chat_id,
        "message": message,
        "send_at": send_at.isoformat(),
        "context": context,
    }
    with _lock:
        _jobs.append(job)
        _save_jobs()
    logger.info("Scheduled job %s for %s", job["id"], job["send_at"])
    return {"ok": True, "job_id": job["id"], "send_at": job["send_at"]}


# ---------------------------------------------------------------------------
# Background thread
# ---------------------------------------------------------------------------

def _send(chat_id: int, text: str) -> None:
    try:
        resp = http_requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("Could not deliver scheduled message: %s", exc)


def _tick() -> None:
    now = datetime.now(timezone.utc)
    with _lock:
        due = [j for j in _jobs if datetime.fromisoformat(j["send_at"]) <= now]
        for job in due:
            _jobs.remove(job)
        if due:
            _save_jobs()

    for job in due:
        logger.info("Delivering scheduled job %s to chat %s", job["id"], job["chat_id"])
        _send(job["chat_id"], job["message"])


def _run() -> None:
    while True:
        try:
            _tick()
        except Exception as exc:
            logger.error("Scheduler tick error: %s", exc)
        time.sleep(60)


def start() -> None:
    """Load persisted jobs and start the background thread. Call once at startup."""
    global _jobs
    _jobs = _load_jobs()
    t = threading.Thread(target=_run, daemon=True, name="scheduler")
    t.start()
    logger.info("Scheduler started (%d pending job(s))", len(_jobs))
