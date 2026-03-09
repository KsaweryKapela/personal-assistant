import json
import logging
from datetime import datetime

import pytz
from openai import OpenAI

from app.config import OPENAI_API_KEY, OPENAI_MODEL, TIMEZONE
from app.schemas import ExtractedIntent

logger = logging.getLogger(__name__)

_client = OpenAI(api_key=OPENAI_API_KEY)

# Tool definition forces the model to return structured output every time.
_ANALYZE_TOOL = {
    "type": "function",
    "function": {
        "name": "analyze_message",
        "description": (
            "Analyze the user's message. "
            "Detect calendar intent and extract event fields, "
            "or compose a plain chat reply."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "intent": {
                    "type": "string",
                    "enum": ["create_event", "missing_info", "chat"],
                    "description": (
                        "create_event – all required fields present; "
                        "missing_info – it is a calendar request but date or time is missing; "
                        "chat – not a calendar request"
                    ),
                },
                "title": {
                    "type": "string",
                    "description": "Short event title, e.g. 'Meeting with Adam'",
                },
                "date": {
                    "type": "string",
                    "description": "Absolute date in YYYY-MM-DD format",
                },
                "start_time": {
                    "type": "string",
                    "description": "Start time in 24-hour HH:MM format",
                },
                "duration_minutes": {
                    "type": "integer",
                    "description": "Duration in minutes. Default 60 if not specified.",
                },
                "description": {
                    "type": "string",
                    "description": "Optional longer description for the event",
                },
                "location": {
                    "type": "string",
                    "description": "Optional location or meeting link",
                },
                "missing_fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Names of required fields that are absent (date, start_time, title)",
                },
                "follow_up_question": {
                    "type": "string",
                    "description": "Friendly question to ask the user when info is missing",
                },
                "chat_reply": {
                    "type": "string",
                    "description": "Reply text for non-calendar messages",
                },
            },
            "required": ["intent"],
        },
    },
}


def extract_intent(user_message: str) -> ExtractedIntent:
    """Call OpenAI and return a structured ExtractedIntent."""
    tz = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    current_dt = now.strftime("%A, %B %d, %Y %H:%M")

    system_prompt = (
        f"You are a personal calendar assistant.\n"
        f"Current date and time: {current_dt} (timezone: {TIMEZONE}).\n\n"
        "Rules:\n"
        "1. If the user wants to schedule, book, set, add, create, or be reminded about "
        "something, it is a calendar request.\n"
        "2. Resolve relative dates (tomorrow, next Tuesday, Friday, etc.) to absolute "
        "YYYY-MM-DD using the current date above.\n"
        "3. Convert all times to 24-hour HH:MM format.\n"
        "4. Default duration_minutes to 60 unless the user specifies otherwise.\n"
        "5. If it IS a calendar request but date OR time is missing, use intent "
        "'missing_info' and set a clear follow_up_question.\n"
        "6. If it is NOT a calendar request, use intent 'chat' and set chat_reply.\n"
        "7. Always call analyze_message — never reply in plain text."
    )

    response = _client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        tools=[_ANALYZE_TOOL],
        tool_choice={"type": "function", "function": {"name": "analyze_message"}},
    )

    tool_call = response.choices[0].message.tool_calls[0]
    args = json.loads(tool_call.function.arguments)
    logger.info("OpenAI extraction: %s", args)

    return ExtractedIntent(**args)
