"""
Entry point — starts the Telegram bot.

Webhook mode (production):  set WEBHOOK_URL env var, e.g. https://my-app.railway.app
Polling mode  (local dev):  leave WEBHOOK_URL unset

Run with:
    python -m app.main
or:
    uv run python -m app.main
"""

import json
import logging
import os
import sys

from telegram import Update

from app.config import (
    DAILY_ACTIVITY_REVIEW_TIME,
    DAILY_MORNING_CHECK_TIME,
    DAILY_PROFILE_REVIEW_TIME,
    DAILY_SUMMARY_TIME,
    LOG_BOT_TOKEN,
    LOG_CHAT_ID,
    OPENAI_MODEL,
    PORT,
    TELEGRAM_CHAT_ID,
    TIMEZONE,
    WEBHOOK_URL,
)
from app.scheduler import add_recurring_daily_job, start as start_scheduler
from app.telegram_bot import build_app

logging.basicConfig(
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    level=logging.INFO,
)
# Silence noisy third-party loggers
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("googleapiclient.discovery").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


def _sync_profile_to_db() -> None:
    """On every startup: ensure USER_PROFILE env var is saved to the DB.

    If the DB row is missing or empty (e.g. after a DB reset), the env var
    is the source of truth and gets written in. If the DB already has data,
    nothing changes.
    """
    if not TELEGRAM_CHAT_ID:
        return
    raw = os.getenv("USER_PROFILE")
    if not raw:
        logger.warning("Profile sync | USER_PROFILE env var not set — skipping")
        return
    try:
        env_profile = json.loads(raw)
    except Exception as exc:
        logger.warning("Profile sync | USER_PROFILE is not valid JSON | error=%s", exc)
        return

    from app.database import load_profile_from_db, save_profile_to_db

    db_profile = load_profile_from_db(TELEGRAM_CHAT_ID)
    if db_profile:
        logger.info(
            "Profile sync | DB already has data | chat_id=%s | categories=%s",
            TELEGRAM_CHAT_ID, list(db_profile.keys()),
        )
    else:
        save_profile_to_db(TELEGRAM_CHAT_ID, env_profile)
        logger.info(
            "Profile sync | written to DB from env | chat_id=%s | categories=%s",
            TELEGRAM_CHAT_ID, list(env_profile.keys()),
        )


def main() -> None:
    logger.info("=" * 60)
    logger.info("Personal Assistant starting up")
    logger.info("Python version: %s", sys.version)
    logger.info("Config | model=%s | timezone=%s", OPENAI_MODEL, TIMEZONE)
    logger.info(
        "Config | webhook_url=%s | port=%d",
        WEBHOOK_URL or "(polling mode)", PORT,
    )
    logger.info(
        "Config | log_bot=%s",
        "enabled" if (LOG_BOT_TOKEN and LOG_CHAT_ID) else "disabled",
    )
    logger.info("=" * 60)

    if LOG_BOT_TOKEN and LOG_CHAT_ID:
        from app.log_bot import setup as setup_log_bot
        setup_log_bot(LOG_BOT_TOKEN, LOG_CHAT_ID)
        logger.info("Telegram log bot enabled | chat_id=%s", LOG_CHAT_ID)
    else:
        logger.info("Telegram log bot disabled (LOG_BOT_TOKEN/LOG_CHAT_ID not set)")

    from app.database import init_db
    init_db()
    _sync_profile_to_db()

    start_scheduler()

    if TELEGRAM_CHAT_ID:
        add_recurring_daily_job(
            chat_id=TELEGRAM_CHAT_ID,
            time_str=DAILY_MORNING_CHECK_TIME,
            name="morning-checkin",
            message=(
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
                f"Keep it concise — one message. "
                f"After the user replies, do the following:\n"
                f"- Call save_daily_summary with today's date and wake_time inferred from the current time (HH:MM).\n"
                f"- Call log_activity with category='habit', name='morning routine', "
                f"status='completed' or 'skipped' based on what they said.\n"
                f"- If they share mood or energy info, save it to their profile using update_user_profile."
            ),
        )
        logger.info("Morning check-in job registered | chat_id=%s | time=%s", TELEGRAM_CHAT_ID, DAILY_MORNING_CHECK_TIME)
        add_recurring_daily_job(
            chat_id=TELEGRAM_CHAT_ID,
            time_str=DAILY_PROFILE_REVIEW_TIME,
            name="daily-profile-review",
            message=(
                f"[DAILY PROFILE REVIEW — AUTOMATED TASK]\n"
                f"Step 1: Fetch today's full conversation using query_database with this SQL: "
                f"SELECT role, content, timestamp FROM messages WHERE chat_id = {TELEGRAM_CHAT_ID} "
                f"AND timestamp > NOW() - INTERVAL '24 hours' ORDER BY timestamp ASC\n"
                f"Step 2: Read every message carefully. Cross-reference with the current user profile.\n"
                f"Step 3: For every new fact, preference, habit, goal, mood pattern, opinion, "
                f"or personality trait revealed today that isn't already captured in the profile, "
                f"call update_user_profile to record it. Use search_memory to verify something "
                f"isn't already stored before adding it.\n"
                f"Step 4: Send the user a short, direct summary — what you learned about them today "
                f"and exactly which profile fields were updated. Be specific."
            ),
        )
        logger.info("Daily profile review job registered | chat_id=%s | time=%s", TELEGRAM_CHAT_ID, DAILY_PROFILE_REVIEW_TIME)
        add_recurring_daily_job(
            chat_id=TELEGRAM_CHAT_ID,
            time_str=DAILY_ACTIVITY_REVIEW_TIME,
            name="daily-activity-review",
            message=(
                f"[DAILY ACTIVITY REVIEW — AUTOMATED TASK]\n"
                f"Step 1: Fetch today's logged activities using query_database with this SQL: "
                f"SELECT id, category, name, status, notes, metadata, timestamp FROM activities "
                f"WHERE chat_id = {TELEGRAM_CHAT_ID} AND timestamp > NOW() - INTERVAL '24 hours' "
                f"ORDER BY timestamp ASC\n"
                f"Step 2: Also fetch today's messages to understand what the user actually did: "
                f"SELECT role, content, timestamp FROM messages WHERE chat_id = {TELEGRAM_CHAT_ID} "
                f"AND timestamp > NOW() - INTERVAL '24 hours' ORDER BY timestamp ASC\n"
                f"Step 3: Also fetch the user's current task list using list_tasks, so you can "
                f"cross-reference any tasks that may have been completed or worked on today.\n"
                f"Step 4: Cross-reference the activities and tasks against the conversation. Check for: "
                f"wrong status (e.g. marked completed but user said they skipped), missing activities "
                f"(user mentioned doing something that was never logged), duplicate entries, "
                f"inaccurate names or categories, missing notes that would add useful context, "
                f"tasks that the user completed today that should be marked done or logged as activities.\n"
                f"Step 5: Fix everything — use update_activity for corrections, delete_activity for "
                f"duplicates or mistakes, log_activity for anything that was missed.\n"
                f"Step 6: Send the user a concise summary of what was found and what was fixed, "
                f"including any relevant tasks that are still pending. "
                f"If everything looks correct, just say so briefly."
            ),
        )
        logger.info("Daily activity review job registered | chat_id=%s | time=%s", TELEGRAM_CHAT_ID, DAILY_ACTIVITY_REVIEW_TIME)
        add_recurring_daily_job(
            chat_id=TELEGRAM_CHAT_ID,
            time_str=DAILY_SUMMARY_TIME,
            name="daily-summary",
            message=(
                f"[DAILY SUMMARY — AUTOMATED TASK]\n"
                f"Compile a full summary for today ({TELEGRAM_CHAT_ID}).\n\n"
                f"Step 1: Fetch today's activities: SELECT id, category, name, status, notes, metadata, timestamp "
                f"FROM activities WHERE chat_id = {TELEGRAM_CHAT_ID} AND timestamp > NOW() - INTERVAL '24 hours' "
                f"ORDER BY timestamp ASC\n\n"
                f"Step 2: Fetch today's messages: SELECT role, content, timestamp FROM messages "
                f"WHERE chat_id = {TELEGRAM_CHAT_ID} AND timestamp > NOW() - INTERVAL '24 hours' "
                f"ORDER BY timestamp ASC\n\n"
                f"Step 3: Fetch today's calendar events using list_events for today's date.\n\n"
                f"Step 4: From all the above, compute:\n"
                f"- wake_time: use the value already stored in today's daily_summaries row if present "
                f"(set by the morning check-in); otherwise infer from the first message timestamp (HH:MM)\n"
                f"- sleep_time: infer from the last message timestamp (HH:MM)\n"
                f"- sleep_duration_hours: calculate if both known\n"
                f"- activities_completed/skipped/partial/total + completion_rate_pct: count from activities\n"
                f"- workout_done: true if any workout-category activity is completed or completed_late\n"
                f"- deep_work_minutes: sum duration from work-category activity metadata where available\n"
                f"- mood_score / energy_score / stress_score (1–10): infer from conversation tone and content\n"
                f"- overall_score (1–10): holistic rating of the day\n"
                f"- highlights: key wins, good moments, things that went well\n"
                f"- challenges: what was hard, skipped, or didn't go to plan\n"
                f"- key_takeaways: the most important lessons or insights from the day\n"
                f"- summary: 2–3 sentence plain-English overview of the day\n"
                f"- mood_description: free-text description of emotional state and mood throughout the day\n"
                f"- stress_description: free-text description of stress levels, sources, and how handled\n"
                f"- gut_state: what the user ate and how it affected them (only fill if mentioned in conversation)\n\n"
                f"Step 5: Call save_daily_summary with all computed values.\n\n"
                f"Step 6: Send the user a concise end-of-day report — scores, headline stats, and a one-line summary."
            ),
        )
        logger.info("Daily summary job registered | chat_id=%s | time=%s", TELEGRAM_CHAT_ID, DAILY_SUMMARY_TIME)
    else:
        logger.info("Daily profile review disabled (TELEGRAM_CHAT_ID not set)")

    app = build_app()

    if WEBHOOK_URL:
        logger.info("Bot mode | webhook | url=%s | port=%d", WEBHOOK_URL, PORT)
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            webhook_url=WEBHOOK_URL,
            allowed_updates=Update.ALL_TYPES,
        )
    else:
        logger.info("Bot mode | polling (local dev)")
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
