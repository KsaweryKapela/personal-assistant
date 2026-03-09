import logging

from app.calendar_client import create_event
from app.openai_client import extract_intent
from app.schemas import CalendarEventRequest
from app.utils import friendly_datetime

logger = logging.getLogger(__name__)


def process_message(text: str) -> str:
    """
    Main orchestrator: receive raw Telegram text, return the reply string.

    Steps:
      1. Ask OpenAI to classify intent and extract fields.
      2. Route based on intent:
         - chat        → return the AI's chat reply
         - missing_info → return the follow-up question
         - create_event → validate, create calendar event, confirm
    """
    logger.info("Processing message: %r", text[:200])

    try:
        extracted = extract_intent(text)
    except Exception as exc:
        logger.error("OpenAI extraction failed: %s", exc)
        return "Sorry, I couldn't understand that. Could you rephrase?"

    logger.info("Intent=%s  title=%r  date=%s  time=%s",
                extracted.intent, extracted.title, extracted.date, extracted.start_time)

    # --- plain chat ---
    if extracted.intent == "chat":
        return extracted.chat_reply or "I'm here to help! Ask me to schedule something."

    # --- calendar request but info is missing ---
    if extracted.intent == "missing_info":
        return (
            extracted.follow_up_question
            or "I need a bit more info — what date and time should I schedule this?"
        )

    # --- create_event ---
    if extracted.intent == "create_event":
        # Defensive check in case the model missed a field
        missing = []
        if not extracted.title:
            missing.append("title")
        if not extracted.date:
            missing.append("date")
        if not extracted.start_time:
            missing.append("time")

        if missing:
            return (
                extracted.follow_up_question
                or f"Almost there — I still need: {', '.join(missing)}. Could you add that?"
            )

        event_req = CalendarEventRequest(
            title=extracted.title,
            date=extracted.date,
            start_time=extracted.start_time,
            duration_minutes=extracted.duration_minutes or 60,
            description=extracted.description,
            location=extracted.location,
        )

        result = create_event(event_req)

        if not result.ok:
            logger.error("Calendar creation failed: %s", result.error)
            return f"I couldn't add the event to your calendar. Error: {result.error}"

        when = friendly_datetime(extracted.date, extracted.start_time)
        reply = f"Done — added '{extracted.title}' on {when}."
        if result.link:
            reply += f"\n{result.link}"
        return reply

    # Fallback (should not happen with a well-behaved model)
    logger.warning("Unexpected intent: %s", extracted.intent)
    return "I'm not sure what to do with that. Try asking me to schedule something!"
