import logging
import os
from datetime import datetime, timedelta

import pytz
import requests as http_requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.config import GOOGLE_CREDENTIALS_FILE, GOOGLE_TOKEN_FILE, TIMEZONE

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]

_RAILWAY_GQL = "https://backboard.railway.app/graphql/v2"


def _sync_token_to_railway(token_json: str) -> None:
    """Push the refreshed token to Railway env vars so it survives redeployments."""
    api_token = os.getenv("RAILWAY_API_TOKEN")
    project_id = os.getenv("RAILWAY_PROJECT_ID")
    environment_id = os.getenv("RAILWAY_ENVIRONMENT_ID")
    service_id = os.getenv("RAILWAY_SERVICE_ID")

    if not all([api_token, project_id, environment_id, service_id]):
        return  # Not running on Railway or token not configured — skip silently

    mutation = """
    mutation variableUpsert($input: VariableUpsertInput!) {
        variableUpsert(input: $input)
    }
    """
    payload = {
        "query": mutation,
        "variables": {
            "input": {
                "projectId": project_id,
                "environmentId": environment_id,
                "serviceId": service_id,
                "name": "GOOGLE_TOKEN_JSON",
                "value": token_json,
            }
        },
    }
    try:
        resp = http_requests.post(
            _RAILWAY_GQL,
            json=payload,
            headers={"Authorization": f"Bearer {api_token}"},
            timeout=10,
        )
        resp.raise_for_status()
        logger.info("Synced refreshed token to Railway env vars.")
    except Exception as exc:
        logger.warning("Could not sync token to Railway: %s", exc)


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

        token_json = creds.to_json()
        with open(GOOGLE_TOKEN_FILE, "w") as fh:
            fh.write(token_json)
        logger.info("Saved Google token to %s", GOOGLE_TOKEN_FILE)
        _sync_token_to_railway(token_json)

    return build("calendar", "v3", credentials=creds)


def list_events(date: str) -> dict:
    """List all events for a given date (YYYY-MM-DD)."""
    try:
        service = _get_service()
        tz = pytz.timezone(TIMEZONE)

        day = datetime.strptime(date, "%Y-%m-%d")
        start = tz.localize(day.replace(hour=0, minute=0, second=0, microsecond=0))
        end = tz.localize(day.replace(hour=23, minute=59, second=59, microsecond=0))

        result = service.events().list(
            calendarId="primary",
            timeMin=start.isoformat(),
            timeMax=end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
        ).execute()

        events = []
        for e in result.get("items", []):
            start_info = e.get("start", {})
            end_info = e.get("end", {})
            events.append({
                "id": e.get("id"),
                "title": e.get("summary", "(no title)"),
                "start": start_info.get("dateTime", start_info.get("date")),
                "end": end_info.get("dateTime", end_info.get("date")),
                "location": e.get("location"),
                "description": e.get("description"),
                "attendees": [a.get("email") for a in e.get("attendees", [])],
            })

        logger.info("Listed %d events for %s", len(events), date)
        return {"events": events}

    except HttpError as exc:
        logger.error("Google Calendar HTTP error: %s", exc)
        return {"error": f"Google Calendar error: {exc}"}
    except Exception as exc:
        logger.error("Unexpected calendar error: %s", exc)
        return {"error": str(exc)}


def create_event(
    title: str,
    date: str,
    start_time: str,
    duration_minutes: int = 60,
    description: str = None,
    location: str = None,
    attendees: list = None,
) -> dict:
    """Create a calendar event. Returns event id and link."""
    try:
        service = _get_service()
        tz = pytz.timezone(TIMEZONE)

        start_dt = datetime.strptime(f"{date} {start_time}", "%Y-%m-%d %H:%M")
        start_dt = tz.localize(start_dt)
        end_dt = start_dt + timedelta(minutes=duration_minutes)

        body: dict = {
            "summary": title,
            "start": {"dateTime": start_dt.isoformat(), "timeZone": TIMEZONE},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": TIMEZONE},
        }
        if description:
            body["description"] = description
        if location:
            body["location"] = location
        if attendees:
            body["attendees"] = [{"email": e} for e in attendees]

        event = service.events().insert(calendarId="primary", body=body).execute()
        logger.info("Created event id=%s title=%r", event.get("id"), title)
        return {"ok": True, "event_id": event.get("id"), "link": event.get("htmlLink")}

    except HttpError as exc:
        logger.error("Google Calendar HTTP error: %s", exc)
        return {"ok": False, "error": f"Google Calendar error: {exc}"}
    except Exception as exc:
        logger.error("Unexpected calendar error: %s", exc)
        return {"ok": False, "error": str(exc)}


def delete_event(event_id: str) -> dict:
    """Delete a calendar event by ID."""
    try:
        service = _get_service()
        service.events().delete(calendarId="primary", eventId=event_id).execute()
        logger.info("Deleted event id=%s", event_id)
        return {"ok": True}

    except HttpError as exc:
        logger.error("Google Calendar HTTP error: %s", exc)
        return {"ok": False, "error": f"Google Calendar error: {exc}"}
    except Exception as exc:
        logger.error("Unexpected calendar error: %s", exc)
        return {"ok": False, "error": str(exc)}


def update_event(
    event_id: str,
    title: str = None,
    date: str = None,
    start_time: str = None,
    duration_minutes: int = None,
    description: str = None,
    location: str = None,
) -> dict:
    """Update fields on an existing event. Only provided fields are changed."""
    try:
        service = _get_service()
        tz = pytz.timezone(TIMEZONE)

        event = service.events().get(calendarId="primary", eventId=event_id).execute()

        if title is not None:
            event["summary"] = title
        if description is not None:
            event["description"] = description
        if location is not None:
            event["location"] = location

        if date or start_time or duration_minutes:
            # Parse current start/end to fill in any missing pieces
            current_start_str = event.get("start", {}).get("dateTime")
            if current_start_str:
                current_start = datetime.fromisoformat(current_start_str)
                current_end_str = event.get("end", {}).get("dateTime")
                if current_end_str:
                    current_end = datetime.fromisoformat(current_end_str)
                    current_duration = int((current_end - current_start).total_seconds() / 60)
                else:
                    current_duration = 60
                current_date = current_start.strftime("%Y-%m-%d")
                current_time = current_start.strftime("%H:%M")
            else:
                current_date = date or datetime.now(tz).strftime("%Y-%m-%d")
                current_time = start_time or "09:00"
                current_duration = 60

            use_date = date or current_date
            use_time = start_time or current_time
            use_duration = duration_minutes or current_duration

            new_start = datetime.strptime(f"{use_date} {use_time}", "%Y-%m-%d %H:%M")
            new_start = tz.localize(new_start)
            new_end = new_start + timedelta(minutes=use_duration)

            event["start"] = {"dateTime": new_start.isoformat(), "timeZone": TIMEZONE}
            event["end"] = {"dateTime": new_end.isoformat(), "timeZone": TIMEZONE}

        updated = service.events().update(
            calendarId="primary", eventId=event_id, body=event
        ).execute()

        logger.info("Updated event id=%s", event_id)
        return {"ok": True, "event_id": updated.get("id"), "link": updated.get("htmlLink")}

    except HttpError as exc:
        logger.error("Google Calendar HTTP error: %s", exc)
        return {"ok": False, "error": f"Google Calendar error: {exc}"}
    except Exception as exc:
        logger.error("Unexpected calendar error: %s", exc)
        return {"ok": False, "error": str(exc)}


def add_attendees(event_id: str, emails: list) -> dict:
    """Add one or more attendees (by email) to an existing event."""
    try:
        service = _get_service()
        event = service.events().get(calendarId="primary", eventId=event_id).execute()

        existing = {a.get("email") for a in event.get("attendees", [])}
        attendees = list(event.get("attendees", []))
        for email in emails:
            if email not in existing:
                attendees.append({"email": email})

        event["attendees"] = attendees
        updated = service.events().update(
            calendarId="primary", eventId=event_id, body=event
        ).execute()

        logger.info("Added attendees %s to event id=%s", emails, event_id)
        return {"ok": True, "attendees": [a.get("email") for a in updated.get("attendees", [])]}

    except HttpError as exc:
        logger.error("Google Calendar HTTP error: %s", exc)
        return {"ok": False, "error": f"Google Calendar error: {exc}"}
    except Exception as exc:
        logger.error("Unexpected calendar error: %s", exc)
        return {"ok": False, "error": str(exc)}
