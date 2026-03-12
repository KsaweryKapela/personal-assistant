import json
import logging
from collections import deque
from datetime import datetime

import pytz
import requests as http_requests
from openai import OpenAI

from app.calendar_client import add_attendees, create_event, delete_event, list_events, update_event
from app.config import OPENAI_API_KEY, OPENAI_MODEL, TELEGRAM_BOT_TOKEN, TIMEZONE
from app.profile_client import load_profile, save_profile

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
                "Update the user's persistent profile JSON. Call this whenever the user shares new "
                "personal information: preferences, contacts (with email addresses), habits, goals, "
                "or corrections to existing info. Only add new fields or remove outdated ones — "
                "never rewrite the whole profile. Pass the full updated profile object."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "profile": {
                        "type": "object",
                        "description": (
                            "The complete updated profile as a JSON object with category keys "
                            "(e.g. personal, career, health, diet, lifestyle, relationship, "
                            "contacts, personality, assistant_preferences). "
                            "Only modify the relevant fields."
                        ),
                    }
                },
                "required": ["profile"],
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
]

def _update_user_profile(profile: dict) -> dict:
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

    system_prompt = (
        f"You are a personal assistant for the user described below.\n\n"
        f"=== USER PROFILE ===\n{json.dumps(profile, indent=2, ensure_ascii=False)}\n\n"
        f"=== CONTEXT ===\n"
        f"Current date and time: {current_dt} (timezone: {TIMEZONE}).\n"
        f"{calendar_context}\n\n"
        "=== INSTRUCTIONS ===\n"
        "Use the available tools to fulfil the user's request. "
        "Resolve relative dates (tomorrow, next Friday, etc.) to absolute YYYY-MM-DD. "
        "Use 24-hour HH:MM for times. "
        "If you need to find an event ID before acting on it, call list_events first. "
        "When creating events involving known contacts, auto-add their emails as attendees. "
        "When the user shares new personal info, preferences, or contacts (including email addresses), "
        "call update_user_profile with the full updated profile JSON — only add or remove the relevant "
        "fields, never rewrite unrelated parts. "
        "When the user asks to see their profile, call send_profile. "
        "After completing all actions, reply with a short, friendly confirmation."
    )

    # Build per-call dispatch table (includes chat_id-bound send_profile)
    tool_dispatch = {**_TOOL_DISPATCH_BASE, "send_profile": _build_send_profile(chat_id)}

    # Retrieve or initialise conversation history for this chat
    if chat_id not in _history:
        _history[chat_id] = deque(maxlen=_HISTORY_LIMIT)

    history = _history[chat_id]
    history.append({"role": "user", "content": user_message})

    messages = [{"role": "system", "content": system_prompt}] + list(history)

    # Agentic loop
    while True:
        response = _client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            tools=_TOOLS,
            tool_choice="auto",
        )

        msg = response.choices[0].message
        messages.append(msg)

        if not msg.tool_calls:
            reply = msg.content or "Done."
            history.append({"role": "assistant", "content": reply})
            return reply

        for tc in msg.tool_calls:
            fn = tool_dispatch.get(tc.function.name)
            if fn is None:
                result = {"error": f"Unknown tool: {tc.function.name}"}
            else:
                args = json.loads(tc.function.arguments)
                logger.info("Tool call: %s(%s)", tc.function.name, args)
                result = fn(**args)
                logger.info("Tool result: %s", result)

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result),
            })
