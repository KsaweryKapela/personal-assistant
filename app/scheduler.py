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


def add_recurring_daily_job(chat_id: int, message: str, time_str: str, name: str) -> None:
    """Register a daily recurring job. No-op if a job with this name already exists (idempotent on restart)."""
    with _lock:
        if any(j.get("name") == name for j in _jobs):
            logger.info("Recurring job already registered | name=%r", name)
            return

    import pytz
    from datetime import timedelta
    from app.config import TIMEZONE

    tz = pytz.timezone(TIMEZONE)
    h, m = map(int, time_str.split(":"))
    now = datetime.now(tz)
    next_run = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if next_run <= now:
        # Missed today's slot — fire in 1 min regardless of how long ago it was.
        # After firing, _reschedule_daily pushes it to tomorrow, so it won't
        # re-fire even if the app restarts again later today.
        next_run = now + timedelta(minutes=1)

    job = {
        "id": str(uuid.uuid4()),
        "chat_id": chat_id,
        "name": name,
        "message": message,
        "send_at": next_run.isoformat(),
        "context": "",
        "repeat_daily_at": time_str,
    }
    with _lock:
        _jobs.append(job)
        _save_jobs()
    logger.info("Recurring job registered | name=%r | first_run=%s", name, next_run.isoformat())


# ---------------------------------------------------------------------------
# Background thread
# ---------------------------------------------------------------------------

def _send(chat_id: int, text: str, job_id: str) -> None:
    from app.utils import send_telegram
    logger.info("Scheduler delivery | start | job_id=%s | chat_id=%s | message_len=%d", job_id, chat_id, len(text))
    t0 = time.monotonic()
    result = send_telegram(chat_id, text)
    if result["ok"]:
        logger.info("Scheduler delivery | ok | job_id=%s | chat_id=%s | duration=%.2fs", job_id, chat_id, time.monotonic() - t0)
    else:
        logger.warning("Scheduler delivery | failed | job_id=%s | chat_id=%s | duration=%.2fs | error=%s", job_id, chat_id, time.monotonic() - t0, result.get("error"))


def _reschedule_daily(job: dict) -> None:
    """Re-add a recurring daily job for its next occurrence (tomorrow at the same time)."""
    import pytz
    from datetime import timedelta
    from app.config import TIMEZONE

    tz = pytz.timezone(TIMEZONE)
    time_str = job["repeat_daily_at"]
    h, m = map(int, time_str.split(":"))
    now = datetime.now(tz)
    next_run = now.replace(hour=h, minute=m, second=0, microsecond=0) + timedelta(days=1)

    new_job = {**job, "id": str(uuid.uuid4()), "send_at": next_run.isoformat()}
    with _lock:
        _jobs.append(new_job)
        _save_jobs()
    logger.info("Scheduler recurring | rescheduled | name=%r | next_run=%s", job.get("name"), next_run.isoformat())


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
        _send(chat_id, f"[Scheduled job '{job.get('name')}' failed: {exc}]", job_id)
    finally:
        if job.get("repeat_daily_at"):
            _reschedule_daily(job)


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


# Names of recurring system jobs that cannot be cancelled by the user
_PROTECTED_JOB_NAMES = {"morning-checkin", "daily-profile-review", "daily-activity-review", "daily-summary"}


def remove_job(job_id: str) -> dict:
    """Cancel a pending scheduled check-in by job ID. Refuses to remove protected system jobs."""
    with _lock:
        job = next((j for j in _jobs if j["id"] == job_id), None)
        if job is None:
            return {"ok": False, "error": f"No pending job with id={job_id!r}"}
        if job.get("name") in _PROTECTED_JOB_NAMES:
            return {"ok": False, "error": f"Job {job['name']!r} is a protected system job and cannot be cancelled."}
        _jobs.remove(job)
        _save_jobs()
    logger.info("remove_job | ok | job_id=%s | name=%r", job_id, job.get("name"))
    return {"ok": True, "removed": job.get("name")}


def start() -> None:
    """Load persisted jobs and start the background thread. Call once at startup."""
    global _jobs
    _jobs = _load_jobs()
    t = threading.Thread(target=_run, daemon=True, name="scheduler")
    t.start()
    logger.info("Scheduler started | pending_jobs=%d", len(_jobs))
