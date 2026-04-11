"""
Morning wake-up watcher.

Polls Oura from 04:00 every 5 minutes to detect when the user has woken up.
Once wake is detected:  schedules the morning check-in at wake_time + 30 min.
Fallback at 09:00:      fires the standard check-in if Oura hasn't synced.
"""

import logging
from datetime import datetime, timedelta

import pytz

from app.config import TIMEZONE
from app.oura_client import get_daily_oura_data

logger = logging.getLogger(__name__)

_FALLBACK_HOUR = 6       # fire without Oura data if no sync by this hour
_POLL_INTERVAL_MIN = 5   # minutes between Oura polls
_CHECKIN_NAMES = {"morning-checkin-oura", "morning-checkin-fallback"}


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

def _prompt_with_oura(oura: dict) -> str:
    tz = pytz.timezone(TIMEZONE)

    wake_str = "unknown"
    if oura.get("wake_time"):
        try:
            wake_str = datetime.fromisoformat(oura["wake_time"]).astimezone(tz).strftime("%H:%M")
        except (ValueError, TypeError):
            pass

    sleep_total = oura.get("sleep_total_min")
    if sleep_total:
        h, m = divmod(sleep_total, 60)
        sleep_str = f"{h}h{m:02d}m" if m else f"{h}h"
    else:
        sleep_str = "unknown"

    readiness = oura.get("readiness_score")
    sleep_score = oura.get("sleep_score")
    resting_hr = oura.get("sleep_resting_hr")
    hrv = oura.get("sleep_avg_hrv")

    oura_summary = (
        f"wake_time={wake_str}, sleep={sleep_str}, readiness={readiness}/100, "
        f"sleep_score={sleep_score}, resting_hr={resting_hr} bpm, hrv={hrv} ms"
    )

    return (
        f"[MORNING CHECK-IN — AUTOMATED TASK]\n"
        f"Oura Ring data: {oura_summary}\n\n"
        f"Step 1: Load the user profile using get_user_profile and read their morning routine.\n"
        f"Step 2: Send a single warm, brief morning message that:\n"
        f"  a. Opens with a natural, human observation about their night — weave in the wake time "
        f"({wake_str}), sleep duration ({sleep_str}), and readiness score ({readiness}) "
        f"conversationally. Make it feel like a friend noticed, not a data readout.\n"
        f"  b. Reminds them of their specific morning routine habits from their profile.\n"
        f"  c. Asks:\n"
        f"     1. Did they complete their morning routine?\n"
        f"     2. How are they feeling — mood and energy?\n"
        f"     3. What are their main plans or priorities for today?\n"
        f"Keep it concise — one message.\n"
        f"After the user replies:\n"
        f"- Call log_activity with category='habit', name='morning routine', "
        f"status='completed' or 'skipped' based on what they said.\n"
        f"- If they share mood or energy info, save it to profile using update_user_profile."
    )


def _prompt_no_oura() -> str:
    return (
        f"[MORNING CHECK-IN — AUTOMATED TASK]\n"
        f"Step 1: Load the user profile using get_user_profile and read their morning routine "
        f"(look in lifestyle, health, or any relevant section).\n"
        f"Step 2: Send the user a single warm, brief morning message that:\n"
        f"  a. Greets them and reminds them of their morning routine (list the specific habits/steps "
        f"     from their profile — e.g. cold shower, meditation, journaling, etc.).\n"
        f"  b. Asks three things:\n"
        f"     1. Did they complete their morning routine? (reference the specific items)\n"
        f"     2. How are they feeling — mood and energy level?\n"
        f"     3. What are their main plans or priorities for today?\n"
        f"Keep it concise — one message.\n"
        f"After the user replies:\n"
        f"- Call log_activity with category='habit', name='morning routine', "
        f"status='completed' or 'skipped' based on what they said.\n"
        f"- If they share mood or energy info, save it to their profile using update_user_profile."
    )


# ---------------------------------------------------------------------------
# Watcher logic
# ---------------------------------------------------------------------------

def _checkin_already_scheduled() -> bool:
    from app.scheduler import get_pending_jobs
    return any(j.get("name") in _CHECKIN_NAMES for j in get_pending_jobs())


def run_morning_watcher(job: dict) -> None:
    """
    Called by the scheduler each time a morning-watcher or morning-watcher-poll job fires.
    Checks Oura for wake-up and either schedules the check-in or queues the next poll.
    """
    from app.scheduler import add_job, add_watcher_poll_job

    tz = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    chat_id = job["chat_id"]
    today = now.strftime("%Y-%m-%d")

    # Guard against duplicate check-ins (e.g. overlapping polls)
    if _checkin_already_scheduled():
        logger.info("Morning watcher | check-in already scheduled, skipping | chat_id=%s", chat_id)
        return

    logger.info("Morning watcher | polling Oura | chat_id=%s | local_time=%s", chat_id, now.strftime("%H:%M"))

    oura = get_daily_oura_data(today)
    wake_time_raw = oura.get("wake_time")

    if wake_time_raw:
        # Wake detected — schedule check-in at wake_time + 30 min
        try:
            wake_dt = datetime.fromisoformat(wake_time_raw).astimezone(tz)
            checkin_at = wake_dt + timedelta(minutes=30)
            # If the check-in time has already passed (late sync), fire in 1 min
            if checkin_at <= now:
                checkin_at = now + timedelta(minutes=1)
            add_job(chat_id, _prompt_with_oura(oura), checkin_at, name="morning-checkin-oura")
            logger.info(
                "Morning watcher | wake at %s | check-in scheduled for %s | chat_id=%s",
                wake_dt.strftime("%H:%M"), checkin_at.strftime("%H:%M"), chat_id,
            )
        except (ValueError, TypeError) as exc:
            logger.error("Morning watcher | bad wake_time %r | %s — falling back", wake_time_raw, exc)
            _fire_fallback(chat_id, now)
        return

    # No wake yet — check fallback cutoff
    fallback_dt = now.replace(hour=_FALLBACK_HOUR, minute=0, second=0, microsecond=0)
    if now >= fallback_dt:
        logger.info("Morning watcher | no Oura sync by %02d:00 | firing standard check-in | chat_id=%s", _FALLBACK_HOUR, chat_id)
        _fire_fallback(chat_id, now)
    else:
        next_poll = now + timedelta(minutes=_POLL_INTERVAL_MIN)
        add_watcher_poll_job(chat_id, next_poll)
        logger.info(
            "Morning watcher | no wake yet | next poll at %s | chat_id=%s",
            next_poll.strftime("%H:%M"), chat_id,
        )


def _fire_fallback(chat_id: int, now: datetime) -> None:
    from app.scheduler import add_job
    fire_at = now + timedelta(minutes=1)
    add_job(chat_id, _prompt_no_oura(), fire_at, name="morning-checkin-fallback")
