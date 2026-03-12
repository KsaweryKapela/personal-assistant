import json
import logging
from collections import deque
from datetime import datetime, timedelta

import pytz
import requests as http_requests
from openai import OpenAI

from app.calendar_client import add_attendees, create_event, delete_event, list_events, update_event
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
            "name": "schedule_message",
            "description": (
                "Schedule a proactive message to be sent to the user at a future time. "
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
                        "description": "The message to send. Keep it short and friendly.",
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
]

def _update_user_profile(action: str, category: str, key: str | None = None, value=None) -> dict:
    profile = load_profile()
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
    return save_profile(profile)


def _build_send_profile(chat_id: int):
    """Return a closure that sends the current profile JSON to the given chat."""
    def send_profile() -> dict:
        profile = load_profile()
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
            logger.warning("Could not send profile via Telegram: %s", exc)
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
        return add_job(chat_id, message, dt, context, name)
    return schedule_message


_TOOL_DISPATCH_BASE = {
    "list_events": list_events,
    "create_event": create_event,
    "delete_event": delete_event,
    "update_event": update_event,
    "add_attendees": add_attendees,
    "update_user_profile": _update_user_profile,
}


def run_agent(user_message: str, chat_id: int = 0) -> str:
    """Run the calendar agent. Loops until the model stops calling tools."""
    logger.info("Agent run started | chat_id=%s | message=%r", chat_id, user_message[:200])
    tz = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    today_str = now.strftime("%Y-%m-%d")
    current_dt = now.strftime("%A, %B %d, %Y %H:%M")

    # Pre-load today's events as context
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

    profile = load_profile()

    pending = get_pending_jobs()
    if pending:
        pending_lines = "\n".join(
            f"  [{j.get('name', 'unnamed')}] {j['send_at']} — {j['message']}"
            for j in pending
        )
        scheduled_context = f"Scheduled check-ins:\n{pending_lines}"
    else:
        scheduled_context = "No check-ins scheduled."

    system_prompt = (
        f"You are a personal assistant for the user described below.\n\n"
        f"=== USER PROFILE ===\n{json.dumps(profile, indent=2, ensure_ascii=False)}\n\n"
        f"=== CONTEXT ===\n"
        f"Current date and time: {current_dt} (timezone: {TIMEZONE}).\n"
        f"{calendar_context}\n"
        f"{scheduled_context}\n\n"
        "=== INSTRUCTIONS ===\n"
        "Use the available tools to fulfil the user's request. "
        "Resolve relative dates (tomorrow, next Friday, etc.) to absolute YYYY-MM-DD. "
        "Use 24-hour HH:MM for times. "
        "If you need to find an event ID before acting on it, call list_events first. "
        "When creating events involving known contacts, auto-add their emails as attendees. "
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
        "save it to the profile using update_user_profile.\n\n"
        "Tone: gentle, supportive, concise. Short messages. Never pushy."
    )

    # Build per-call dispatch table (includes chat_id-bound closures)
    tool_dispatch = {
        **_TOOL_DISPATCH_BASE,
        "send_profile": _build_send_profile(chat_id),
        "schedule_message": _build_schedule_message(chat_id),
    }

    # Retrieve or initialise conversation history for this chat
    if chat_id not in _history:
        _history[chat_id] = deque(maxlen=_HISTORY_LIMIT)

    history = _history[chat_id]
    history.append({"role": "user", "content": user_message})

    messages = [{"role": "system", "content": system_prompt}] + list(history)

    # Agentic loop
    iteration = 0
    while True:
        iteration += 1

        # Log every message being sent to the LLM (skip system prompt — too large)
        loggable = [m for m in messages if not isinstance(m, dict) or m.get("role") != "system"]
        for m in loggable:
            if isinstance(m, dict):
                role = m.get("role", "?")
                content = m.get("content") or ""
                logger.info("  [%s] %s", role, str(content)[:300])
            else:
                # OpenAI message object (assistant turn with possible tool_calls)
                role = getattr(m, "role", "?")
                content = getattr(m, "content", "") or ""
                tool_calls = getattr(m, "tool_calls", None)
                if tool_calls:
                    names = [tc.function.name for tc in tool_calls]
                    logger.info("  [%s] (tool_calls: %s)", role, names)
                else:
                    logger.info("  [%s] %s", role, str(content)[:300])

        logger.info("Sending %d message(s) to %s (iter=%d)", len(messages), OPENAI_MODEL, iteration)
        response = _client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            tools=_TOOLS,
            tool_choice="auto",
        )

        usage = response.usage
        if usage:
            logger.info(
                "Tokens: prompt=%d completion=%d total=%d",
                usage.prompt_tokens, usage.completion_tokens, usage.total_tokens,
            )

        msg = response.choices[0].message
        messages.append(msg)

        if not msg.tool_calls:
            reply = msg.content or "Done."
            history.append({"role": "assistant", "content": reply})
            logger.info("Final reply (iter=%d): %r", iteration, reply[:300])
            return reply

        logger.info("%d tool call(s) requested", len(msg.tool_calls))
        for tc in msg.tool_calls:
            fn = tool_dispatch.get(tc.function.name)
            if fn is None:
                result = {"error": f"Unknown tool: {tc.function.name}"}
                logger.warning("Unknown tool requested: %s", tc.function.name)
            else:
                args = json.loads(tc.function.arguments)
                logger.info("  CALL → %s | args: %s", tc.function.name, args)
                result = fn(**args)
                logger.info("  RESULT ← %s | %s", tc.function.name, result)

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result),
            })
