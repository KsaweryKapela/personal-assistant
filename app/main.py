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
from app.scheduler import add_recurring_daily_job, register_morning_watcher, start as start_scheduler
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
        register_morning_watcher(TELEGRAM_CHAT_ID)
        logger.info("Morning watcher registered | chat_id=%s | starts=04:00 | fallback=09:00", TELEGRAM_CHAT_ID)
        add_recurring_daily_job(
            chat_id=TELEGRAM_CHAT_ID,
            time_str=DAILY_PROFILE_REVIEW_TIME,
            name="daily-profile-review",
            message=(
                f"[DAILY PROFILE REVIEW — AUTOMATED TASK]\n"
                f"Step 1: Fetch today's full conversation using query_database with this SQL: "
                f"SELECT role, content, timestamp FROM messages WHERE chat_id = {TELEGRAM_CHAT_ID} "
                f"AND timestamp > NOW() - INTERVAL '24 hours' ORDER BY timestamp ASC\n"
                f"Step 2: Read the full current user profile.\n"
                f"Step 3: Review every field in the profile. For each one ask: is this still accurate? "
                f"is it redundant with another entry? is it vague or could be stated more precisely? "
                f"If the profile has grown bloated — many overlapping, wordy, or redundant entries — "
                f"delete the weaker ones and rewrite the survivors to be sharper. "
                f"Consolidate related entries into one better entry where it makes sense. "
                f"Use update_user_profile with action='delete' and action='set' as needed. "
                f"A lean, precise profile is the goal — don't leave obvious redundancy in place.\n"
                f"Step 4: From today's conversation, identify new insights about the user — "
                f"patterns, preferences, habits, goals, values, tendencies, or personality traits. "
                f"One-off events do NOT belong here, but a recurring pattern or clear preference "
                f"revealed even over the past few days is worth adding. "
                f"Use search_memory to check if it's already covered before adding.\n"
                f"Step 5: Send the user a short, direct summary — what was rewritten, deleted, added, "
                f"and why. List the actual changes made."
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
                f"Step 3: Fetch the user's current task list using list_tasks. "
                f"Identify tasks completed today (status='completed' and completed_at matches today's date). "
                f"Also note any tasks that are still pending but were due today or earlier.\n"
                f"Step 4: Cross-reference activities, tasks, and the conversation. Check for:\n"
                f"- Wrong activity status (e.g. marked completed but user said they skipped)\n"
                f"- Missing activities: user mentioned doing something that was never logged\n"
                f"- Completed tasks that have no matching logged activity — these must be logged\n"
                f"- Duplicate activity entries\n"
                f"- Inaccurate names, categories, or missing notes\n"
                f"Step 5: Fix everything:\n"
                f"- Use update_activity for corrections\n"
                f"- Use delete_activity for duplicates or mistakes\n"
                f"- Use log_activity for missing activities (including each task completed today "
                f"that has no matching activity — log it with status='completed', category inferred "
                f"from the task title, and a note referencing the task)\n"
                f"Step 6: Send the user a concise summary of what was found and fixed, "
                f"including any tasks still pending. If everything looks correct, say so briefly."
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
                f"Step 4: Call get_oura_data for today's date to retrieve Oura Ring health metrics.\n\n"
                f"Step 5: From all the above, compute:\n"
                f"- activities_completed/skipped/partial/total + completion_rate_pct: count from activities\n"
                f"- workout_done: true if any workout-category activity is completed or completed_late\n"
                f"- mood_score / energy_score / stress_score / gut_score / relationship_score (1–10): infer from conversation tone and content\n"
                f"- overall_score (1–10): holistic rating of the day\n"
                f"- highlights: key wins, good moments, things that went well\n"
                f"- challenges: what was hard, skipped, or didn't go to plan\n"
                f"- key_takeaways: the most important lessons or insights from the day\n"
                f"- summary: 2–3 sentence plain-English overview of the day\n"
                f"- mood_description: free-text description of emotional state and mood throughout the day\n"
                f"- stress_description: free-text description of stress levels, sources, and how handled\n"
                f"- gut_state: what the user ate and how it affected them (only fill if mentioned in conversation)\n"
                f"- relationship_description: free-text description of social interactions and relationship quality (only fill if mentioned in conversation)\n"
                f"- metadata: include the full Oura data dict under the key 'oura', plus any other extra structured data worth keeping\n\n"
                f"Step 6: Call save_daily_summary passing ALL of the above fields — "
                f"activities_completed, activities_skipped, activities_partial, activities_total, "
                f"completion_rate_pct, workout_done, mood_score, energy_score, stress_score, "
                f"gut_score, relationship_score, overall_score, highlights, challenges, "
                f"key_takeaways, summary, mood_description, stress_description, gut_state, "
                f"relationship_description, metadata. "
                f"Do not omit any field — use null/empty string/0 as appropriate if data is unavailable.\n\n"
                f"Step 7: Send the user a concise end-of-day report. Include:\n"
                f"- Scores: mood, energy, stress, overall (1–10)\n"
                f"- Activity: completion rate, workout done\n"
                f"- Oura health (if available): sleep duration + score, wake time, resting HR, HRV, readiness score, steps, active calories, meditation sessions\n"
                f"- One-line summary of the day"
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
