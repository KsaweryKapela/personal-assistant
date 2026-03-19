import json
import logging
import time
from collections import deque
from datetime import datetime, timedelta

import pytz
import requests as http_requests
from openai import OpenAI

from app.calendar_client import add_attendees, create_event, create_task, delete_event, delete_task, list_events, list_tasks, update_event
from app.config import (
    DAILY_ACTIVITY_REVIEW_TIME,
    DAILY_MORNING_CHECK_TIME,
    DAILY_PROFILE_REVIEW_TIME,
    DAILY_SUMMARY_TIME,
    OPENAI_API_KEY,
    OPENAI_MODEL,
    TELEGRAM_BOT_TOKEN,
    TIMEZONE,
)
from app.profile_client import load_profile, save_profile
from app.scheduler import add_job, get_pending_jobs, remove_job

logger = logging.getLogger(__name__)

_client = OpenAI(api_key=OPENAI_API_KEY)

# Per-chat conversation history: chat_id -> deque of {role, content} messages
_HISTORY_LIMIT = 20  # messages (10 exchanges)
_history: dict[int, deque] = {}

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "update_user_profile",
            "description": (
                "Add, update, or delete a single field in the user's profile. "
                "Call this whenever the user shares new personal info or asks to remove something. "
                "Use action='set' to add or update, action='delete' to remove. "
                "Omit 'key' with action='delete' to remove an entire category."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["set", "delete"],
                        "description": "'set' to add/update a field, 'delete' to remove it.",
                    },
                    "category": {
                        "type": "string",
                        "description": (
                            "Top-level profile category, e.g. 'personal', 'career', 'health', "
                            "'diet', 'lifestyle', 'relationship', 'contacts', 'personality', "
                            "'assistant_preferences'. A new category is created automatically."
                        ),
                    },
                    "key": {
                        "type": "string",
                        "description": (
                            "Field name within the category. Omit only when deleting an entire category."
                        ),
                    },
                    "value": {
                        "description": "New value for the field (required for action='set'). Can be any JSON type.",
                    },
                },
                "required": ["action", "category"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_profile",
            "description": (
                "Send the user's full profile JSON as a formatted Telegram message. "
                "Call this when the user asks to see their profile."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_events",
            "description": "List all calendar events for a given date.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Date in YYYY-MM-DD format.",
                    }
                },
                "required": ["date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_event",
            "description": "Create a new calendar event.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Event title."},
                    "date": {"type": "string", "description": "Date in YYYY-MM-DD format."},
                    "start_time": {"type": "string", "description": "Start time in HH:MM (24h)."},
                    "duration_minutes": {"type": "integer", "description": "Duration in minutes. Default 60."},
                    "description": {"type": "string", "description": "Optional event description."},
                    "location": {"type": "string", "description": "Optional location or meeting link."},
                    "attendees": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of attendee email addresses.",
                    },
                    "color": {
                        "type": "string",
                        "enum": [
                            "lavender", "sage", "grape", "flamingo", "banana",
                            "tangerine", "peacock", "graphite", "blueberry", "basil", "tomato",
                        ],
                        "description": (
                            "Optional event color. Pick based on type: "
                            "workout/sport → flamingo, "
                            "work/focus/meeting → blueberry, "
                            "meal/food → banana, "
                            "social/fun → grape, "
                            "health/medical → tomato, "
                            "personal/habit → sage, "
                            "travel → peacock, "
                            "other → lavender."
                        ),
                    },
                },
                "required": ["title", "date", "start_time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_event",
            "description": "Delete a calendar event by its ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string", "description": "The event ID to delete."}
                },
                "required": ["event_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_event",
            "description": (
                "Update one or more fields of an existing calendar event. "
                "Only the fields you provide will be changed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string", "description": "The event ID to update."},
                    "title": {"type": "string", "description": "New event title."},
                    "date": {"type": "string", "description": "New date in YYYY-MM-DD format."},
                    "start_time": {"type": "string", "description": "New start time in HH:MM (24h)."},
                    "duration_minutes": {"type": "integer", "description": "New duration in minutes."},
                    "description": {"type": "string", "description": "New description."},
                    "location": {"type": "string", "description": "New location or meeting link."},
                },
                "required": ["event_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_attendees",
            "description": "Add one or more people to an existing calendar event.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string", "description": "The event ID."},
                    "emails": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of email addresses to add.",
                    },
                },
                "required": ["event_id", "emails"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_task",
            "description": (
                "Create a task in the user's Google Tasks list (visible in Google Calendar). "
                "Use for to-dos, action items, or things without a fixed time slot. "
                "For time-specific events, use create_event instead. "
                "ALWAYS call list_tasks first to check for duplicates before creating."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Task title."},
                    "notes": {"type": "string", "description": "Optional notes or details."},
                    "due_date": {"type": "string", "description": "Optional due date in YYYY-MM-DD format."},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": "List all tasks in the user's Google Tasks list — both pending and recently completed — including their IDs, status, due date, and completion time.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_task",
            "description": (
                "Delete a task from the user's Google Tasks list by its ID. "
                "Call list_tasks first if you don't have the task ID."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "The Google Tasks task ID to delete."},
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_message",
            "description": (
                "Schedule a proactive AI-generated message to the user at a future time. "
                "At the scheduled time the AI will be prompted with 'message' and will "
                "compose a fresh, context-aware reply (with calendar, activities, and profile "
                "all loaded) before sending it to the user. "
                "Use for check-ins, follow-ups, or reminders. "
                "Provide either delay_minutes (e.g. 60 for 1 hour from now) "
                "or send_at (ISO datetime string). Do not spam — a few meaningful "
                "check-ins per day is enough."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": (
                            "The internal prompt/instruction given to the AI at the scheduled time. "
                            "Write it as a directive, e.g. 'Check in with the user about how their gym "
                            "session went. Be warm and brief.' The AI will read the current context and "
                            "craft the actual message — do NOT write the final user-facing text here."
                        ),
                    },
                    "delay_minutes": {
                        "type": "integer",
                        "description": "Send this many minutes from now. Use instead of send_at.",
                    },
                    "send_at": {
                        "type": "string",
                        "description": "Exact send time as ISO datetime (e.g. '2026-03-12T18:00:00'). Use instead of delay_minutes.",
                    },
                    "context": {
                        "type": "string",
                        "description": "Optional note about why this was scheduled (for your own reference).",
                    },
                    "name": {
                        "type": "string",
                        "description": "Short label for this job, e.g. 'gym check-in', 'evening reflection'. Required.",
                    },
                },
                "required": ["message", "name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_scheduled_jobs",
            "description": "Return all currently scheduled jobs (both system daily jobs and user-created check-ins), including their IDs, names, and scheduled times.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_checkin",
            "description": (
                "Cancel (delete) a pending scheduled check-in by its job ID. "
                "Use the job IDs shown in 'Scheduled check-ins' context. "
                "Cannot cancel the hardcoded daily system jobs (morning check-in, profile review, "
                "activity review, daily summary) — only user-created check-ins can be removed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "The job ID to cancel (from the scheduled check-ins list)."},
                },
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "log_activity",
            "description": (
                "Log a user activity to the database. Call this when the user reports "
                "completing, skipping, or partially doing something — gym, deep work, "
                "reading, meals, walks, habits, etc. Always log after a check-in response."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "Activity category: workout, work, meal, reading, walk, social, habit, or other.",
                    },
                    "name": {
                        "type": "string",
                        "description": "Short descriptive name, e.g. 'gym session', 'deep work block', 'morning walk'.",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["completed", "completed_late", "skipped", "partial"],
                        "description": "completed = done on time, completed_late = done but late, skipped = not done, partial = started but not finished.",
                    },
                    "notes": {
                        "type": "string",
                        "description": "Optional freeform notes, e.g. 'felt tired', 'hit new PR on bench'.",
                    },
                    "metadata": {
                        "type": "object",
                        "description": "Optional structured data, e.g. {\"duration_minutes\": 60, \"weight_kg\": 100}.",
                    },
                    "start_time": {
                        "type": "string",
                        "description": "Optional start time of the activity in HH:MM (24h), e.g. '08:00'.",
                    },
                    "end_time": {
                        "type": "string",
                        "description": "Optional end time of the activity in HH:MM (24h), e.g. '09:30'.",
                    },
                },
                "required": ["category", "name", "status"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_activity",
            "description": (
                "Delete an activity record from the database by its ID. "
                "Use when the user says a logged activity was a mistake or wants it removed. "
                "First use query_database to find the record ID if you don't already have it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "activity_id": {
                        "type": "integer",
                        "description": "The numeric ID of the activity to delete (from the 'id' column).",
                    },
                },
                "required": ["activity_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_activity",
            "description": (
                "Edit an existing activity record. Use when the user wants to correct a field "
                "(e.g. wrong status, name, or notes). Only pass the fields that need changing. "
                "First use query_database to find the record ID if you don't already have it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "activity_id": {
                        "type": "integer",
                        "description": "The numeric ID of the activity to update (from the 'id' column).",
                    },
                    "category": {
                        "type": "string",
                        "description": "New category value.",
                    },
                    "name": {
                        "type": "string",
                        "description": "New name value.",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["completed", "completed_late", "skipped", "partial"],
                        "description": "New status value.",
                    },
                    "notes": {
                        "type": "string",
                        "description": "New notes value.",
                    },
                    "metadata": {
                        "type": "object",
                        "description": "New metadata object.",
                    },
                    "start_time": {
                        "type": "string",
                        "description": "New start time in HH:MM (24h).",
                    },
                    "end_time": {
                        "type": "string",
                        "description": "New end time in HH:MM (24h).",
                    },
                },
                "required": ["activity_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_stats",
            "description": "Get activity statistics and completion rates for a given period.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "Filter by category (optional). Omit for all categories.",
                    },
                    "period_days": {
                        "type": "integer",
                        "description": "How many days back to look. Default 7.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_memory",
            "description": (
                "Semantic search across all stored messages and activities. "
                "Use when the user asks about past events, feelings, or patterns "
                "that may not be in recent context."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language search query.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results to return. Default 5.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_daily_summary",
            "description": (
                "Save (or overwrite) the daily summary record for a given date. "
                "Call this at the end of the daily summary job after computing all stats. "
                "All fields except date are optional — only pass what you have."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "The date being summarised, YYYY-MM-DD."},
                    "wake_time": {"type": "string", "description": "Inferred wake time, HH:MM."},
                    "sleep_time": {"type": "string", "description": "Inferred sleep/last-active time, HH:MM."},
                    "sleep_duration_hours": {"type": "number", "description": "Hours of sleep (float)."},
                    "activities_completed": {"type": "integer"},
                    "activities_skipped": {"type": "integer"},
                    "activities_partial": {"type": "integer"},
                    "activities_total": {"type": "integer"},
                    "completion_rate_pct": {"type": "integer", "description": "0–100."},
                    "workout_done": {"type": "boolean"},
                    "deep_work_minutes": {"type": "integer"},
                    "mood_score": {"type": "integer", "description": "1–10."},
                    "energy_score": {"type": "integer", "description": "1–10."},
                    "stress_score": {"type": "integer", "description": "1–10."},
                    "overall_score": {"type": "integer", "description": "1–10 holistic day rating."},
                    "highlights": {"type": "string", "description": "Key wins or good moments."},
                    "challenges": {"type": "string", "description": "What was hard or didn't go well."},
                    "key_takeaways": {"type": "string", "description": "The most important lessons or insights from the day."},
                    "summary": {"type": "string", "description": "2–3 sentence plain-English day overview."},
                    "mood_description": {"type": "string", "description": "Free-text description of the user's mood and emotional state throughout the day."},
                    "stress_description": {"type": "string", "description": "Free-text description of stress levels, sources, and how they were handled."},
                    "gut_state": {"type": "string", "description": "What the user ate and how it made them feel (digestion, energy, wellbeing). Only fill if the user mentioned food or gut state."},
                    "metadata": {"type": "object", "description": "Any extra stats that don't fit the schema."},
                },
                "required": ["date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_database",
            "description": (
                "Run a read-only SELECT query directly on the database. "
                "Tables: activities (id, chat_id, timestamp, category, name, status, notes, metadata, start_time, end_time), "
                "messages (id, chat_id, timestamp, role, content, message_type), "
                "profile (chat_id, data, updated_at), "
                "daily_summaries (id, chat_id, date, wake_time, sleep_time, sleep_duration_hours, "
                "activities_completed, activities_skipped, activities_partial, activities_total, "
                "completion_rate_pct, workout_done, deep_work_minutes, mood_score, energy_score, "
                "stress_score, overall_score, highlights, challenges, key_takeaways, summary, "
                "mood_description, stress_description, gut_state, metadata, created_at). "
                "Use this when the user wants to inspect raw records."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "A SELECT SQL query. Only SELECT is allowed.",
                    },
                },
                "required": ["sql"],
            },
        },
    },
]

def _build_update_user_profile(chat_id: int):
    def update_user_profile(action: str, category: str, key: str | None = None, value=None) -> dict:
        profile = load_profile(chat_id)
        if action == "set":
            if key is None:
                return {"ok": False, "error": "key is required for action='set'"}
            if category not in profile or not isinstance(profile[category], dict):
                profile[category] = {}
            profile[category][key] = value
        elif action == "delete":
            if key is None:
                profile.pop(category, None)
            elif category in profile and isinstance(profile[category], dict):
                profile[category].pop(key, None)
        return save_profile(profile, chat_id)
    return update_user_profile


def _build_send_profile(chat_id: int):
    """Return a closure that sends the current profile JSON to the given chat."""
    import html as _html
    from app.utils import send_telegram
    _CHUNK = 3900  # leave room for <pre> tags and safe margin
    def send_profile() -> dict:
        profile = load_profile(chat_id)
        logger.info("send_profile | chat_id=%s | profile_keys=%s | profile_len=%d",
                    chat_id, list(profile.keys()), len(profile))
        json_str = _html.escape(json.dumps(profile, indent=2, ensure_ascii=False))
        chunks = [json_str[i:i + _CHUNK] for i in range(0, max(len(json_str), 1), _CHUNK)]
        logger.info("send_profile | json_len=%d | chunks=%d", len(json_str), len(chunks))
        for chunk in chunks:
            result = send_telegram(chat_id, f"<pre>{chunk}</pre>", parse_mode="HTML")
            if not result["ok"]:
                return result
        return {"ok": True}
    return send_profile


def _build_schedule_message(chat_id: int):
    """Return a closure that schedules a message for the given chat."""
    def schedule_message(
        message: str,
        name: str,
        delay_minutes: int | None = None,
        send_at: str | None = None,
        context: str = "",
    ) -> dict:
        tz = pytz.timezone(TIMEZONE)
        now = datetime.now(tz)
        if delay_minutes is not None:
            dt = now + timedelta(minutes=delay_minutes)
        elif send_at:
            dt = datetime.fromisoformat(send_at)
            if dt.tzinfo is None:
                dt = tz.localize(dt)
        else:
            return {"ok": False, "error": "Provide either delay_minutes or send_at."}
        return add_job(chat_id, message, dt, context, name)
    return schedule_message


_TOOL_DISPATCH_BASE = {
    "list_events": list_events,
    "list_tasks": list_tasks,
    "create_event": create_event,
    "create_task": create_task,
    "delete_task": delete_task,
    "delete_event": delete_event,
    "update_event": update_event,
    "add_attendees": add_attendees,
    "cancel_checkin": remove_job,
    "list_scheduled_jobs": lambda: {"jobs": [
        {"id": j["id"], "name": j.get("name", "unnamed"), "send_at": j["send_at"], "repeat_daily_at": j.get("repeat_daily_at")}
        for j in get_pending_jobs()
    ]},
}


def run_agent(user_message: str, chat_id: int = 0, request_id: str = "", message_type: str = "text") -> str:
    """Run the calendar agent. Loops until the model stops calling tools."""
    p = f"[req={request_id}] " if request_id else ""

    tz = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    today_str = now.strftime("%Y-%m-%d")
    current_dt = now.strftime("%A, %B %d, %Y %H:%M")

    # ---- Context loading ----
    try:
        today = list_events(today_str)
        events = today.get("events", [])
        if events:
            lines = []
            for e in events:
                line = f"  [{e['id']}] {e['title']}  {e['start']} → {e['end']}"
                if e.get("attendees"):
                    line += f"  (attendees: {', '.join(e['attendees'])})"
                lines.append(line)
            calendar_context = "Today's events:\n" + "\n".join(lines)
        else:
            calendar_context = "No events scheduled for today."
    except Exception as exc:
        calendar_context = f"(Could not load today's events: {exc})"
        logger.warning("%sCalendar context failed | %s", p, exc)

    profile = load_profile(chat_id)
    pending = get_pending_jobs()
    if pending:
        pending_lines = "\n".join(
            f"  [id={j['id']}] [{j.get('name', 'unnamed')}] {j['send_at']} — {j['message']}"
            for j in pending
        )
        scheduled_context = f"Scheduled check-ins:\n{pending_lines}"
    else:
        scheduled_context = "No check-ins scheduled."

    try:
        from app.database import get_recent_activities
        recent_acts = get_recent_activities(chat_id, limit=5)
        if recent_acts:
            act_lines = "\n".join(
                f"  [{a['timestamp'][:16]}] {a['category']} | {a['name']} | {a['status']}"
                for a in recent_acts
            )
            activity_context = f"Recent logged activities:\n{act_lines}"
        else:
            activity_context = "No activities logged yet."
    except Exception as exc:
        activity_context = "(Could not load activity log)"
        logger.warning("%sActivity context failed | %s", p, exc)

    # ---- System prompt construction ----
    system_prompt = (
        f"You are a personal assistant for the user described below.\n\n"
        f"=== USER PROFILE ===\n{json.dumps(profile, ensure_ascii=False)}\n\n"
        f"=== CONTEXT ===\n"
        f"Current date and time: {current_dt} (timezone: {TIMEZONE}).\n"
        f"{calendar_context}\n"
        f"{scheduled_context}\n"
        f"{activity_context}\n"
        f"Automated daily jobs (all times in {TIMEZONE}):\n"
        f"- {DAILY_MORNING_CHECK_TIME}: morning check-in — asks wake time, mood, energy, and today's plans. "
        f"Saves wake_time to daily_summaries and logs a 'wake up' activity.\n"
        f"- {DAILY_PROFILE_REVIEW_TIME}: profile review — reads today's messages, updates the user profile with new insights.\n"
        f"- {DAILY_ACTIVITY_REVIEW_TIME}: activity review — cross-references logged activities against the conversation, fixes errors.\n"
        f"- {DAILY_SUMMARY_TIME}: daily summary — computes all day stats, saves to daily_summaries, sends a report.\n\n"
        "=== INSTRUCTIONS ===\n"
        "IMPORTANT: Only act on the LAST user message. Conversation history is provided for context only — "
        "never repeat, re-execute, or duplicate any action that was already performed in a previous turn. "
        "If you already created an event, logged an activity, or scheduled a message in an earlier turn, do not do it again.\n"
        "Context injected above is intentionally minimal to preserve the context window. "
        "Use search_memory and query_database proactively whenever you need historical information — "
        "never ask the user for details you could retrieve with a tool call.\n"
        "Use the available tools to fulfil the user's request. "
        "Resolve relative dates (tomorrow, next Friday, etc.) to absolute YYYY-MM-DD. "
        "Use 24-hour HH:MM for times. "
        "If you need to find an event ID before acting on it, call list_events first. "
        "When creating events involving known contacts, auto-add their emails as attendees. "
        "When creating calendar events, always set a color that matches the event type "
        "(workout→flamingo, work/meeting→blueberry, meal→banana, social→grape, health→tomato, personal/habit→sage, travel→peacock). "
        "For time-specific activities, use create_event. For to-dos without a fixed time, use create_task. "
        "Before creating a task, always call list_tasks to check for duplicates — do not create a task that already exists. "
        "When the user shares new personal info, preferences, or contacts (including email addresses), "
        "call update_user_profile once per changed field using action='set' or action='delete'. "
        "Never pass the full profile — only the specific category, key, and value being changed. "
        "When the user asks to see their profile, call send_profile.\n\n"
        "=== MEMORY & RAG ===\n"
        "Before answering any question about the user's past (mood, habits, patterns, events, feelings, "
        "progress), ALWAYS call search_memory first — never answer from context alone. "
        "Use query_stats for habit/activity trends. "
        "Use query_database when you need specific records or IDs.\n\n"
        "=== PROACTIVE BEHAVIOUR ===\n"
        "You care about the user's wellbeing and help them live a calm, balanced day. "
        "Use schedule_message to check in proactively — but keep it minimal (2–3 meaningful messages per day max). "
        "Always check 'Scheduled check-ins' in context before scheduling — do not create duplicates. "
        "If there are no check-ins scheduled at all, proactively schedule at least one appropriate one. "
        "Good moments to schedule a check-in:\n"
        "- After a planned activity (gym, deep work block, meeting) — ask how it went.\n"
        "- Mid-afternoon if the day looks heavy — suggest a short break or walk.\n"
        "- Evening (~20:00) — a brief reflection on the day.\n"
        "- Whenever the user says 'remind me', 'check back', 'ask me later', etc.\n"
        "When the user responds to a check-in and shares something meaningful (mood, progress, habits), "
        "call log_activity to record it AND save key insights to the profile using update_user_profile.\n\n"
        "=== ACTIVITY LOGGING ===\n"
        "Always call log_activity when the user reports on an activity (completed, skipped, late, partial). "
        "Always include start_time and end_time in HH:MM when the user mentions or implies when something started or ended. "
        "Use delete_activity when the user says an activity was logged by mistake or asks to remove it. "
        "Use update_activity when the user wants to correct a field (status, name, notes, start_time, end_time, etc.). "
        "For both, use query_database first to find the record ID if needed.\n\n"
        "=== CREATIVE THINKING ===\n"
        "When the user shares a problem, challenge, or goal, don't just acknowledge it — analyse it. "
        "Use search_memory and the profile to understand what has already been tried or is in place. "
        "Then propose concrete, novel solutions they haven't considered yet. "
        "Think from first principles. Be specific — no vague encouragement.\n\n"
        "=== COMMUNICATION STYLE ===\n"
        "Never ask follow-up questions. Make a judgment call and act or respond. "
        "If you need more context, use your tools to find it — never ask the user. "
        "Be direct and decisive. Keep messages short. No filler phrases. "
        "Do not use any markdown formatting — no **bold**, no _italic_, no headers, no bullet dashes, no backticks. "
        "Plain text only. Telegram does not render markdown and it will appear as raw symbols."
    )

    if message_type == "scheduled":
        system_prompt = system_prompt.replace(
            "=== PROACTIVE BEHAVIOUR ===\n"
            "You care about the user's wellbeing and help them live a calm, balanced day. "
            "Use schedule_message to check in proactively — but keep it minimal (2–3 meaningful messages per day max). "
            "Always check 'Scheduled check-ins' in context before scheduling — do not create duplicates. "
            "If there are no check-ins scheduled at all, proactively schedule at least one appropriate one. "
            "Good moments to schedule a check-in:\n"
            "- After a planned activity (gym, deep work block, meeting) — ask how it went.\n"
            "- Mid-afternoon if the day looks heavy — suggest a short break or walk.\n"
            "- Evening (~20:00) — a brief reflection on the day.\n"
            "- Whenever the user says 'remind me', 'check back', 'ask me later', etc.\n"
            "When the user responds to a check-in and shares something meaningful (mood, progress, habits), "
            "call log_activity to record it AND save key insights to the profile using update_user_profile.\n\n",
            "=== PROACTIVE BEHAVIOUR ===\n"
            "You are running as an AUTOMATED SCHEDULED TASK. "
            "Do NOT schedule any new check-ins or messages — only execute the steps in the prompt above.\n\n",
        )

    # Build per-call dispatch table (includes chat_id-bound closures)
    from app.database import (
        log_activity, query_stats, search_memory, run_query,
        delete_activity, update_activity, save_daily_summary,
    )

    def _log_activity(**kwargs): return log_activity(chat_id, **kwargs)
    def _query_stats(**kwargs): return query_stats(chat_id, **kwargs)
    def _search_memory(**kwargs): return search_memory(chat_id, **kwargs)
    def _delete_activity(**kwargs): return delete_activity(chat_id, **kwargs)
    def _update_activity(**kwargs): return update_activity(chat_id, **kwargs)
    def _save_daily_summary(**kwargs): return save_daily_summary(chat_id, **kwargs)

    tool_dispatch = {
        **_TOOL_DISPATCH_BASE,
        "update_user_profile": _build_update_user_profile(chat_id),
        "send_profile": _build_send_profile(chat_id),
        "schedule_message": _build_schedule_message(chat_id),
        "log_activity": _log_activity,
        "delete_activity": _delete_activity,
        "update_activity": _update_activity,
        "save_daily_summary": _save_daily_summary,
        "query_stats": _query_stats,
        "search_memory": _search_memory,
        "query_database": run_query,
    }

    # ---- History ----
    if chat_id not in _history:
        _history[chat_id] = deque(maxlen=_HISTORY_LIMIT)
    history = _history[chat_id]
    history.append({"role": "user", "content": user_message})
    messages = [{"role": "system", "content": system_prompt}] + list(history)

    # ---- Log full LLM input ----
    history_msgs = [m for m in messages if isinstance(m, dict) and m.get("role") != "system"]
    history_text = "\n".join(f"[{m['role']}] {m['content']}" for m in history_msgs) or "(none)"
    logger.info(
        "%s=== LLM INPUT ===\n--- SYSTEM PROMPT ---\n%s\n--- CONVERSATION HISTORY (%d messages) ---\n%s",
        p, system_prompt, len(history_msgs), history_text,
    )

    # ---- Agentic loop ----
    iteration = 0
    t_agent = time.monotonic()

    while True:
        iteration += 1

        response = _client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            tools=_TOOLS,
            tool_choice="auto",
        )

        usage = response.usage
        if usage:
            logger.info(
                "%sTokens | iter=%d | prompt=%d completion=%d total=%d",
                p, iteration, usage.prompt_tokens, usage.completion_tokens, usage.total_tokens,
            )

        msg = response.choices[0].message
        messages.append(msg)

        # ---- No tool calls → done ----
        if not msg.tool_calls:
            reply = (msg.content or "").strip() or "Done."
            history.append({"role": "assistant", "content": reply})
            logger.info("%s=== FINAL REPLY (%.2fs) ===\n%s", p, time.monotonic() - t_agent, reply)
            return reply

        # ---- Tool calls ----
        for tc in msg.tool_calls:
            fn = tool_dispatch.get(tc.function.name)
            if fn is None:
                logger.warning("%sUnknown tool: %s", p, tc.function.name)
                result = {"error": f"Unknown tool: {tc.function.name}"}
            else:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError as exc:
                    result = {"error": f"Could not parse tool arguments: {exc}"}
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result)})
                    continue
                logger.info(
                    "%s=== TOOL CALL [iter=%d] ===\n%s\n%s",
                    p, iteration, tc.function.name,
                    json.dumps(args, indent=2, ensure_ascii=False),
                )
                try:
                    result = fn(**args)
                except Exception as tool_exc:
                    logger.error("%sTOOL ERROR %s | %s", p, tc.function.name, tool_exc, exc_info=True)
                    result = {"error": str(tool_exc)}
                else:
                    logger.info(
                        "%s=== TOOL RESULT [%s] ===\n%s",
                        p, tc.function.name,
                        json.dumps(result, default=str, indent=2, ensure_ascii=False),
                    )

            messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result, default=str)})
