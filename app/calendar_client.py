import logging
import os
from datetime import datetime, timedelta

import pytz
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.config import GOOGLE_CREDENTIALS_FILE, GOOGLE_TOKEN_FILE, TIMEZONE
from app.schemas import CalendarEventRequest, CalendarEventResult

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def _get_service():
    """Authenticate and return a Google Calendar service object."""
    creds = None

    if os.path.exists(GOOGLE_TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("Refreshing Google OAuth token.")
            creds.refresh(Request())
        else:
            if not os.path.exists(GOOGLE_CREDENTIALS_FILE):
                raise FileNotFoundError(
                    f"Google credentials file not found: {GOOGLE_CREDENTIALS_FILE}\n"
                    "Download it from Google Cloud Console and set GOOGLE_CREDENTIALS_FILE."
                )
            logger.info("Starting Google OAuth flow — browser will open.")
            flow = InstalledAppFlow.from_client_secrets_file(GOOGLE_CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(GOOGLE_TOKEN_FILE, "w") as fh:
            fh.write(creds.to_json())
        logger.info("Saved Google token to %s", GOOGLE_TOKEN_FILE)

    return build("calendar", "v3", credentials=creds)


def create_event(request: CalendarEventRequest) -> CalendarEventResult:
    """Create an event on the primary Google Calendar and return the result."""
    try:
        service = _get_service()
        tz = pytz.timezone(TIMEZONE)

        start_dt = datetime.strptime(f"{request.date} {request.start_time}", "%Y-%m-%d %H:%M")
        start_dt = tz.localize(start_dt)
        end_dt = start_dt + timedelta(minutes=request.duration_minutes)

        body: dict = {
            "summary": request.title,
            "start": {"dateTime": start_dt.isoformat(), "timeZone": TIMEZONE},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": TIMEZONE},
        }
        if request.description:
            body["description"] = request.description
        if request.location:
            body["location"] = request.location

        event = service.events().insert(calendarId="primary", body=body).execute()
        logger.info("Created event id=%s title=%r", event.get("id"), request.title)

        return CalendarEventResult(
            event_id=event.get("id"),
            link=event.get("htmlLink"),
        )

    except HttpError as exc:
        logger.error("Google Calendar HTTP error: %s", exc)
        return CalendarEventResult(error=f"Google Calendar error: {exc}")
    except Exception as exc:
        logger.error("Unexpected calendar error: %s", exc)
        return CalendarEventResult(error=str(exc))
