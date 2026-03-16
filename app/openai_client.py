import json
import logging
import time
from collections import deque
from datetime import datetime, timedelta

import pytz
import requests as http_requests
from openai import OpenAI

from app.calendar_client import add_attendees, create_event, create_task, delete_event, list_events, update_event
from app.config import OPENAI_API_KEY, OPENAI_MODEL, TELEGRAM_BOT_TOKEN, TIMEZONE
from app.profile_client import load_profile, save_profile
from app.scheduler import add_job, get_pending_jobs

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
                "For time-specific events, use create_event instead."
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
            "name": "query_database",
            "description": (
                "Run a read-only SELECT query directly on the database. "
                "Tables: activities (id, chat_id, timestamp, category, name, status, notes, metadata), "
                "messages (id, chat_id, timestamp, role, content, message_type), "
                "profile (chat_id, data, updated_at). "
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
    def send_profile() -> dict:
        profile = load_profile(chat_id)
        text = f"```json\n{json.dumps(profile, indent=2, ensure_ascii=False)}\n```"
        try:
            resp = http_requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
                timeout=10,
            )
            resp.raise_for_status()
            return {"ok": True}
        except Exception as exc:
            logger.warning("send_profile failed | chat_id=%s | %s", chat_id, exc)
            return {"ok": False, "error": str(exc)}
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
        logger.info("schedule_message | name=%r | send_at=%s | msg=%r", name, dt.isoformat(), message)
        return add_job(chat_id, message, dt, context, name)
    return schedule_message


_TOOL_DISPATCH_BASE = {
    "list_events": list_events,
    "create_event": create_event,
    "create_task": create_task,
    "delete_event": delete_event,
    "update_event": update_event,
    "add_attendees": add_attendees,
}


def run_agent(user_message: str, chat_id: int = 0, request_id: str = "") -> str:
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
            f"  [{j.get('name', 'unnamed')}] {j['send_at']} — {j['message']}"
            for j in pending
        )
        scheduled_context = f"Scheduled check-ins:\n{pending_lines}"
    else:
        scheduled_context = "No check-ins scheduled."

    try:
        from app.database import get_recent_activities
        recent_acts = get_recent_activities(chat_id, limit=10)
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
        f"=== USER PROFILE ===\n{json.dumps(profile, indent=2, ensure_ascii=False)}\n\n"
        f"=== CONTEXT ===\n"
        f"Current date and time: {current_dt} (timezone: {TIMEZONE}).\n"
        f"{calendar_context}\n"
        f"{scheduled_context}\n"
        f"{activity_context}\n\n"
        "=== INSTRUCTIONS ===\n"
        "Use the available tools to fulfil the user's request. "
        "Resolve relative dates (tomorrow, next Friday, etc.) to absolute YYYY-MM-DD. "
        "Use 24-hour HH:MM for times. "
        "If you need to find an event ID before acting on it, call list_events first. "
        "When creating events involving known contacts, auto-add their emails as attendees. "
        "When creating calendar events, always set a color that matches the event type "
        "(workout→flamingo, work/meeting→blueberry, meal→banana, social→grape, health→tomato, personal/habit→sage, travel→peacock). "
        "For time-specific activities, use create_event. For to-dos without a fixed time, use create_task. "
        "When the user shares new personal info, preferences, or contacts (including email addresses), "
        "call update_user_profile once per changed field using action='set' or action='delete'. "
        "Never pass the full profile — only the specific category, key, and value being changed. "
        "When the user asks to see their profile, call send_profile.\n\n"
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
        "Use query_stats when asked about patterns, habits, or progress. "
        "Use search_memory for questions about past events or feelings. "
        "Use query_database when the user wants to inspect raw records. "
        "Use delete_activity when the user says an activity was logged by mistake or asks to remove it — "
        "first use query_database to find the record ID if needed.\n\n"
        "Tone: gentle, supportive, concise. Short messages. Never pushy."
    )

    # Build per-call dispatch table (includes chat_id-bound closures)
    from app.database import log_activity, query_stats, search_memory, run_query, delete_activity

    def _log_activity(**kwargs): return log_activity(chat_id, **kwargs)
    def _query_stats(**kwargs): return query_stats(chat_id, **kwargs)
    def _search_memory(**kwargs): return search_memory(chat_id, **kwargs)
    def _delete_activity(**kwargs): return delete_activity(chat_id, **kwargs)

    tool_dispatch = {
        **_TOOL_DISPATCH_BASE,
        "update_user_profile": _build_update_user_profile(chat_id),
        "send_profile": _build_send_profile(chat_id),
        "schedule_message": _build_schedule_message(chat_id),
        "log_activity": _log_activity,
        "delete_activity": _delete_activity,
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

    # ---- Agentic loop ----
    iteration = 0
    t_agent = time.monotonic()

    while True:
        iteration += 1

        # Log system prompt + full context on first iteration only
        if iteration == 1:
            logger.info("%sSYSTEM PROMPT:\n%s", p, system_prompt)
            non_system = [m for m in messages if not (isinstance(m, dict) and m.get("role") == "system")]
            logger.info("%sCONTEXT (%d messages):", p, len(non_system))
            for m in non_system:
                if isinstance(m, dict):
                    role = m.get("role", "?")
                    content = str(m.get("content") or "")
                    logger.info("%s  [%s] %s", p, role, content[:300])
                else:
                    role = getattr(m, "role", "?")
                    tool_calls = getattr(m, "tool_calls", None)
                    if tool_calls:
                        logger.info("%s  [%s] tool_calls: %s", p, role, [tc.function.name for tc in tool_calls])
                    else:
                        logger.info("%s  [%s] %s", p, role, str(getattr(m, "content", "") or "")[:300])

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
            reply = msg.content or "Done."
            history.append({"role": "assistant", "content": reply})
            logger.info("%sFINAL REPLY (%.2fs):\n%s", p, time.monotonic() - t_agent, reply)
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
                logger.info("%sCALL → %s | %s", p, tc.function.name, json.dumps(args, ensure_ascii=False)[:300])
                try:
                    result = fn(**args)
                except Exception as tool_exc:
                    logger.error("%sTOOL ERROR %s | %s", p, tc.function.name, tool_exc, exc_info=True)
                    result = {"error": str(tool_exc)}
                else:
                    logger.info("%sRESULT ← %s | %s", p, tc.function.name, json.dumps(result, default=str, ensure_ascii=False)[:300])

            messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result, default=str)})
