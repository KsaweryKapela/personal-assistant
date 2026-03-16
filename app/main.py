"""
Entry point — starts the Telegram bot.

Webhook mode (production):  set WEBHOOK_URL env var, e.g. https://my-app.railway.app
Polling mode  (local dev):  leave WEBHOOK_URL unset

Run with:
    python -m app.main
or:
    uv run python -m app.main
"""

import logging
import sys

from telegram import Update

from app.config import LOG_BOT_TOKEN, LOG_CHAT_ID, OPENAI_MODEL, PORT, TELEGRAM_CHAT_ID, TIMEZONE, WEBHOOK_URL
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

    start_scheduler()

    if TELEGRAM_CHAT_ID:
        add_recurring_daily_job(
            chat_id=TELEGRAM_CHAT_ID,
            time_str="23:30",
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
        logger.info("Daily profile review job registered | chat_id=%s | time=23:30", TELEGRAM_CHAT_ID)
        add_recurring_daily_job(
            chat_id=TELEGRAM_CHAT_ID,
            time_str="23:45",
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
                f"Step 3: Cross-reference the activities against the conversation. Check for: "
                f"wrong status (e.g. marked completed but user said they skipped), missing activities "
                f"(user mentioned doing something that was never logged), duplicate entries, "
                f"inaccurate names or categories, missing notes that would add useful context.\n"
                f"Step 4: Fix everything — use update_activity for corrections, delete_activity for "
                f"duplicates or mistakes, log_activity for anything that was missed.\n"
                f"Step 5: Send the user a concise summary of what was found and what was fixed. "
                f"If everything looks correct, just say so briefly."
            ),
        )
        logger.info("Daily activity review job registered | chat_id=%s | time=23:45", TELEGRAM_CHAT_ID)
        add_recurring_daily_job(
            chat_id=TELEGRAM_CHAT_ID,
            time_str="23:55",
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
                f"- wake_time / sleep_time: infer from first and last message timestamps (HH:MM)\n"
                f"- sleep_duration_hours: calculate if both known\n"
                f"- activities_completed/skipped/partial/total + completion_rate_pct: count from activities\n"
                f"- workout_done: true if any workout-category activity is completed or completed_late\n"
                f"- deep_work_minutes: sum duration from work-category activity metadata where available\n"
                f"- mood_score / energy_score / stress_score (1–10): infer from conversation tone and content\n"
                f"- overall_score (1–10): holistic rating of the day\n"
                f"- highlights: key wins, good moments, things that went well\n"
                f"- challenges: what was hard, skipped, or didn't go to plan\n"
                f"- summary: 2–3 sentence plain-English overview of the day\n\n"
                f"Step 5: Call save_daily_summary with all computed values.\n\n"
                f"Step 6: Send the user a concise end-of-day report — scores, headline stats, and a one-line summary."
            ),
        )
        logger.info("Daily summary job registered | chat_id=%s | time=23:55", TELEGRAM_CHAT_ID)
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
