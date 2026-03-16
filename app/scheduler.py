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
                jobs = json.load(f)
            logger.info("Scheduler storage | loaded %d job(s)", len(jobs))
            return jobs
        except Exception as exc:
            logger.warning("Scheduler storage | failed to load %s | error=%s", _SCHEDULER_FILE, exc)
    return []


def _save_jobs() -> None:
    """Must be called while holding _lock."""
    try:
        with open(_SCHEDULER_FILE, "w") as f:
            json.dump(_jobs, f, indent=2)
        logger.debug("Scheduler storage | saved %d job(s) to %s", len(_jobs), _SCHEDULER_FILE)
    except Exception as exc:
        logger.warning("Scheduler storage | failed to save %s | error=%s", _SCHEDULER_FILE, exc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def add_job(chat_id: int, message: str, send_at: datetime, context: str = "", name: str = "") -> dict:
    """Add a new scheduled message job."""
    job = {
        "id": str(uuid.uuid4()),
        "chat_id": chat_id,
        "name": name,
        "message": message,
        "send_at": send_at.isoformat(),
        "context": context,
    }
    with _lock:
        _jobs.append(job)
        _save_jobs()
    logger.info(
        "add_job | ok | job_id=%s | name=%r | chat_id=%s | send_at=%s | message=%r | context=%r",
        job["id"], name, chat_id, job["send_at"], message[:80], context[:80],
    )
    return {"ok": True, "job_id": job["id"], "send_at": job["send_at"]}


# ---------------------------------------------------------------------------
# Background thread
# ---------------------------------------------------------------------------

def _send(chat_id: int, text: str, job_id: str) -> None:
    logger.info("Scheduler delivery | start | job_id=%s | chat_id=%s | message_len=%d", job_id, chat_id, len(text))
    t0 = time.monotonic()
    try:
        resp = http_requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
        resp.raise_for_status()
        logger.info(
            "Scheduler delivery | ok | job_id=%s | chat_id=%s | status=%d | duration=%.2fs",
            job_id, chat_id, resp.status_code, time.monotonic() - t0,
        )
    except Exception as exc:
        logger.warning(
            "Scheduler delivery | failed | job_id=%s | chat_id=%s | duration=%.2fs | error=%s",
            job_id, chat_id, time.monotonic() - t0, exc,
        )


def _run_job(job: dict) -> None:
    """Prompt the AI with the scheduled message and send its response to the user."""
    from app.assistant import process_message  # lazy import to avoid circular dependency

    chat_id = job["chat_id"]
    prompt = job["message"]
    job_id = job["id"]
    logger.info(
        "Scheduler job | prompting AI | job_id=%s | chat_id=%s | prompt=%r",
        job_id, chat_id, prompt[:80],
    )
    try:
        reply = process_message(prompt, chat_id=chat_id, message_type="scheduled")
        _send(chat_id, reply, job_id)
    except Exception as exc:
        logger.error(
            "Scheduler job | AI error | job_id=%s | chat_id=%s | error=%s",
            job_id, chat_id, exc, exc_info=True,
        )


def _tick() -> None:
    now = datetime.now(timezone.utc)
    with _lock:
        due = [j for j in _jobs if datetime.fromisoformat(j["send_at"]) <= now]
        remaining = len(_jobs) - len(due)
        for job in due:
            _jobs.remove(job)
        if due:
            _save_jobs()

    if due:
        logger.info(
            "Scheduler tick | due=%d | remaining=%d | delivering: %s",
            len(due), remaining,
            [{"id": j["id"], "name": j.get("name"), "chat_id": j["chat_id"]} for j in due],
        )
        for job in due:
            _run_job(job)
    else:
        logger.debug("Scheduler tick | due=0 | remaining=%d", remaining)


def _run() -> None:
    logger.info("Scheduler thread | running | poll_interval=60s")
    while True:
        try:
            _tick()
        except Exception as exc:
            logger.error("Scheduler tick | unhandled error | error=%s", exc, exc_info=True)
        time.sleep(60)


def get_pending_jobs() -> list[dict]:
    """Return a snapshot of all pending jobs."""
    with _lock:
        return list(_jobs)


def start() -> None:
    """Load persisted jobs and start the background thread. Call once at startup."""
    global _jobs
    _jobs = _load_jobs()
    t = threading.Thread(target=_run, daemon=True, name="scheduler")
    t.start()
    logger.info("Scheduler started | pending_jobs=%d", len(_jobs))
