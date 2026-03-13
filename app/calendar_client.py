import logging
import os
import time
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
        logger.info("Railway token sync | skipped (Railway env vars not configured)")
        return

    logger.info("Railway token sync | start | project_id=%s | service_id=%s", project_id, service_id)
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
    t0 = time.monotonic()
    try:
        resp = http_requests.post(
            _RAILWAY_GQL,
            json=payload,
            headers={"Authorization": f"Bearer {api_token}"},
            timeout=10,
        )
        resp.raise_for_status()
        logger.info(
            "Railway token sync | ok | status=%d | duration=%.2fs",
            resp.status_code, time.monotonic() - t0,
        )
    except Exception as exc:
        logger.warning(
            "Railway token sync | failed | duration=%.2fs | error=%s",
            time.monotonic() - t0, exc,
        )


def _get_service():
    """Authenticate and return a Google Calendar service object."""
    creds = None

    if os.path.exists(GOOGLE_TOKEN_FILE):
        logger.info("Google auth | loading token from %s", GOOGLE_TOKEN_FILE)
        creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN_FILE, SCOPES)
    else:
        logger.info("Google auth | no token file at %s", GOOGLE_TOKEN_FILE)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("Google auth | token expired, refreshing")
            t0 = time.monotonic()
            creds.refresh(Request())
            logger.info("Google auth | token refreshed | duration=%.2fs", time.monotonic() - t0)
        else:
            if not os.path.exists(GOOGLE_CREDENTIALS_FILE):
                raise FileNotFoundError(
                    f"Google credentials file not found: {GOOGLE_CREDENTIALS_FILE}\n"
                    "Download it from Google Cloud Console and set GOOGLE_CREDENTIALS_FILE."
                )
            logger.info("Google auth | starting OAuth flow (browser will open) | credentials=%s", GOOGLE_CREDENTIALS_FILE)
            flow = InstalledAppFlow.from_client_secrets_file(GOOGLE_CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
            logger.info("Google auth | OAuth flow complete")

        token_json = creds.to_json()
        with open(GOOGLE_TOKEN_FILE, "w") as fh:
            fh.write(token_json)
        logger.info("Google auth | token saved to %s", GOOGLE_TOKEN_FILE)
        _sync_token_to_railway(token_json)
    else:
        logger.debug("Google auth | token valid, reusing")

    return build("calendar", "v3", credentials=creds)


def list_events(date: str) -> dict:
    """List all events for a given date (YYYY-MM-DD)."""
    logger.info("list_events | start | date=%s", date)
    t0 = time.monotonic()
    try:
        service = _get_service()
        tz = pytz.timezone(TIMEZONE)

        day = datetime.strptime(date, "%Y-%m-%d")
        start = tz.localize(day.replace(hour=0, minute=0, second=0, microsecond=0))
        end = tz.localize(day.replace(hour=23, minute=59, second=59, microsecond=0))

        logger.info(
            "list_events | query | calendarId=primary | timeMin=%s | timeMax=%s",
            start.isoformat(), end.isoformat(),
        )

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

        elapsed = time.monotonic() - t0
        logger.info(
            "list_events | ok | date=%s | events=%d | duration=%.2fs",
            date, len(events), elapsed,
        )
        if events:
            logger.info(
                "list_events | event_ids=%s",
                [{"id": e["id"], "title": e["title"]} for e in events],
            )
        return {"events": events}

    except HttpError as exc:
        elapsed = time.monotonic() - t0
        logger.error("list_events | Google API error | date=%s | duration=%.2fs | error=%s", date, elapsed, exc)
        return {"error": f"Google Calendar error: {exc}"}
    except Exception as exc:
        elapsed = time.monotonic() - t0
        logger.error("list_events | unexpected error | date=%s | duration=%.2fs | error=%s", date, elapsed, exc)
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
    logger.info(
        "create_event | start | title=%r | date=%s | start_time=%s | duration_minutes=%d | "
        "location=%r | attendees=%s | has_description=%s",
        title, date, start_time, duration_minutes,
        location, attendees, bool(description),
    )
    t0 = time.monotonic()
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

        logger.info(
            "create_event | inserting | start=%s | end=%s | body_keys=%s",
            start_dt.isoformat(), end_dt.isoformat(), list(body.keys()),
        )
        event = service.events().insert(calendarId="primary", body=body).execute()

        elapsed = time.monotonic() - t0
        logger.info(
            "create_event | ok | event_id=%s | title=%r | duration=%.2fs | link=%s",
            event.get("id"), title, elapsed, event.get("htmlLink"),
        )
        return {"ok": True, "event_id": event.get("id"), "link": event.get("htmlLink")}

    except HttpError as exc:
        elapsed = time.monotonic() - t0
        logger.error("create_event | Google API error | title=%r | duration=%.2fs | error=%s", title, elapsed, exc)
        return {"ok": False, "error": f"Google Calendar error: {exc}"}
    except Exception as exc:
        elapsed = time.monotonic() - t0
        logger.error("create_event | unexpected error | title=%r | duration=%.2fs | error=%s", title, elapsed, exc)
        return {"ok": False, "error": str(exc)}


def delete_event(event_id: str) -> dict:
    """Delete a calendar event by ID."""
    logger.info("delete_event | start | event_id=%s", event_id)
    t0 = time.monotonic()
    try:
        service = _get_service()
        service.events().delete(calendarId="primary", eventId=event_id).execute()
        elapsed = time.monotonic() - t0
        logger.info("delete_event | ok | event_id=%s | duration=%.2fs", event_id, elapsed)
        return {"ok": True}

    except HttpError as exc:
        elapsed = time.monotonic() - t0
        logger.error("delete_event | Google API error | event_id=%s | duration=%.2fs | error=%s", event_id, elapsed, exc)
        return {"ok": False, "error": f"Google Calendar error: {exc}"}
    except Exception as exc:
        elapsed = time.monotonic() - t0
        logger.error("delete_event | unexpected error | event_id=%s | duration=%.2fs | error=%s", event_id, elapsed, exc)
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
    updates = {k: v for k, v in {
        "title": title, "date": date, "start_time": start_time,
        "duration_minutes": duration_minutes, "description": description, "location": location,
    }.items() if v is not None}
    logger.info("update_event | start | event_id=%s | fields_to_update=%s", event_id, list(updates.keys()))
    t0 = time.monotonic()
    try:
        service = _get_service()
        tz = pytz.timezone(TIMEZONE)

        logger.info("update_event | fetching current event | event_id=%s", event_id)
        event = service.events().get(calendarId="primary", eventId=event_id).execute()
        logger.info(
            "update_event | fetched | title=%r | start=%s",
            event.get("summary"), event.get("start", {}).get("dateTime"),
        )

        if title is not None:
            event["summary"] = title
        if description is not None:
            event["description"] = description
        if location is not None:
            event["location"] = location

        if date or start_time or duration_minutes:
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

            logger.info(
                "update_event | rescheduling | new_start=%s | new_end=%s | duration=%dmin",
                new_start.isoformat(), new_end.isoformat(), use_duration,
            )
            event["start"] = {"dateTime": new_start.isoformat(), "timeZone": TIMEZONE}
            event["end"] = {"dateTime": new_end.isoformat(), "timeZone": TIMEZONE}

        updated = service.events().update(
            calendarId="primary", eventId=event_id, body=event
        ).execute()

        elapsed = time.monotonic() - t0
        logger.info(
            "update_event | ok | event_id=%s | updated_fields=%s | duration=%.2fs",
            event_id, list(updates.keys()), elapsed,
        )
        return {"ok": True, "event_id": updated.get("id"), "link": updated.get("htmlLink")}

    except HttpError as exc:
        elapsed = time.monotonic() - t0
        logger.error("update_event | Google API error | event_id=%s | duration=%.2fs | error=%s", event_id, elapsed, exc)
        return {"ok": False, "error": f"Google Calendar error: {exc}"}
    except Exception as exc:
        elapsed = time.monotonic() - t0
        logger.error("update_event | unexpected error | event_id=%s | duration=%.2fs | error=%s", event_id, elapsed, exc)
        return {"ok": False, "error": str(exc)}


def add_attendees(event_id: str, emails: list) -> dict:
    """Add one or more attendees (by email) to an existing event."""
    logger.info("add_attendees | start | event_id=%s | emails=%s", event_id, emails)
    t0 = time.monotonic()
    try:
        service = _get_service()
        logger.info("add_attendees | fetching event | event_id=%s", event_id)
        event = service.events().get(calendarId="primary", eventId=event_id).execute()

        existing = {a.get("email") for a in event.get("attendees", [])}
        logger.info(
            "add_attendees | current_attendees=%s | new_emails=%s",
            list(existing), emails,
        )

        attendees = list(event.get("attendees", []))
        added = []
        already_present = []
        for email in emails:
            if email not in existing:
                attendees.append({"email": email})
                added.append(email)
            else:
                already_present.append(email)

        if already_present:
            logger.info("add_attendees | skipping already present: %s", already_present)

        event["attendees"] = attendees
        updated = service.events().update(
            calendarId="primary", eventId=event_id, body=event
        ).execute()

        elapsed = time.monotonic() - t0
        final_attendees = [a.get("email") for a in updated.get("attendees", [])]
        logger.info(
            "add_attendees | ok | event_id=%s | added=%s | final_attendees=%s | duration=%.2fs",
            event_id, added, final_attendees, elapsed,
        )
        return {"ok": True, "attendees": final_attendees}

    except HttpError as exc:
        elapsed = time.monotonic() - t0
        logger.error("add_attendees | Google API error | event_id=%s | duration=%.2fs | error=%s", event_id, elapsed, exc)
        return {"ok": False, "error": f"Google Calendar error: {exc}"}
    except Exception as exc:
        elapsed = time.monotonic() - t0
        logger.error("add_attendees | unexpected error | event_id=%s | duration=%.2fs | error=%s", event_id, elapsed, exc)
        return {"ok": False, "error": str(exc)}
