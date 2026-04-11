import json
import logging
import os
from datetime import datetime, timedelta

import requests

logger = logging.getLogger(__name__)

_API_BASE = "https://api.ouraring.com/v2/usercollection"
_TOKEN_URL = "https://api.ouraring.com/oauth/token"

OURA_CLIENT_ID: str = os.getenv("OURA_CLIENT_ID", "")
OURA_CLIENT_SECRET: str = os.getenv("OURA_CLIENT_SECRET", "")
OURA_TOKEN_FILE: str = os.getenv("OURA_TOKEN_FILE", "oura_token.json")


def _load_tokens() -> dict:
    if os.path.exists(OURA_TOKEN_FILE):
        with open(OURA_TOKEN_FILE) as f:
            return json.load(f)
    return {}


def _save_tokens(tokens: dict) -> None:
    with open(OURA_TOKEN_FILE, "w") as f:
        json.dump(tokens, f, indent=2)


def _refresh_access_token(refresh_token: str) -> str | None:
    resp = requests.post(_TOKEN_URL, data={
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": OURA_CLIENT_ID,
        "client_secret": OURA_CLIENT_SECRET,
    })
    if resp.status_code == 200:
        data = resp.json()
        _save_tokens(data)
        logger.info("Oura token refreshed successfully")
        return data.get("access_token")
    logger.error("Oura token refresh failed: %s %s", resp.status_code, resp.text)
    return None


def _get(endpoint: str, params: dict) -> dict | None:
    tokens = _load_tokens()
    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")

    if not access_token:
        logger.error(
            "Oura: no access token in %s — make sure OURA_TOKEN_JSON is set and start.sh writes it to disk",
            OURA_TOKEN_FILE,
        )
        return None

    headers = {"Authorization": f"Bearer {access_token}"}
    resp = requests.get(f"{_API_BASE}/{endpoint}", headers=headers, params=params)

    if resp.status_code == 401 and refresh_token:
        logger.info("Oura access token expired, refreshing...")
        new_token = _refresh_access_token(refresh_token)
        if new_token:
            headers = {"Authorization": f"Bearer {new_token}"}
            resp = requests.get(f"{_API_BASE}/{endpoint}", headers=headers, params=params)
        else:
            return None

    if resp.status_code == 200:
        return resp.json()

    logger.error("Oura API %s error %s: %s", endpoint, resp.status_code, resp.text)
    return None


def get_daily_oura_data(date: str | None = None) -> dict:
    """
    Fetch all relevant Oura Ring data for a given date (YYYY-MM-DD).
    Defaults to today. Returns a flat dict suitable for the daily summary metadata.

    Note on date filtering: Oura endpoints have inconsistent boundary behaviour.
    - Sleep sessions are indexed by bedtime_start (may be day-1 for day's sleep).
    - Some endpoints use exclusive end_date.
    We query (date-1) to (date+1) and filter results by item['day'] == date.
    """
    if date is None:
        date = datetime.utcnow().strftime("%Y-%m-%d")

    dt = datetime.strptime(date, "%Y-%m-%d")
    prev_day = (dt - timedelta(days=1)).strftime("%Y-%m-%d")
    next_day = (dt + timedelta(days=1)).strftime("%Y-%m-%d")
    dp = {"start_date": prev_day, "end_date": next_day}

    def first_for_date(data: dict | None) -> dict | None:
        if not data or not data.get("data"):
            return None
        matches = [item for item in data["data"] if item.get("day") == date]
        return matches[0] if matches else None

    result: dict = {"date": date, "available": False}

    try:
        # --- Sleep score + contributors ---
        sleep_daily = _get("daily_sleep", dp)
        s = first_for_date(sleep_daily)
        if s:
            result["sleep_score"] = s.get("score")
            result["sleep_contributors"] = s.get("contributors")

        # --- Detailed sleep session ---
        sleep_session = _get("sleep", dp)
        sess = first_for_date(sleep_session)
        if sess:
            result["sleep_total_min"] = round((sess.get("total_sleep_duration") or 0) / 60)
            result["sleep_deep_min"] = round((sess.get("deep_sleep_duration") or 0) / 60)
            result["sleep_rem_min"] = round((sess.get("rem_sleep_duration") or 0) / 60)
            result["sleep_light_min"] = round((sess.get("light_sleep_duration") or 0) / 60)
            result["sleep_efficiency"] = sess.get("efficiency")
            result["sleep_latency_min"] = round((sess.get("latency") or 0) / 60)
            result["sleep_resting_hr"] = sess.get("lowest_heart_rate")
            result["sleep_avg_hr"] = sess.get("average_heart_rate")
            result["sleep_avg_hrv"] = sess.get("average_hrv")
            result["bedtime"] = sess.get("bedtime_start")
            result["wake_time"] = sess.get("bedtime_end")
            result["restless_periods"] = sess.get("restless_periods")

        # --- Readiness ---
        readiness = _get("daily_readiness", dp)
        r = first_for_date(readiness)
        if r:
            result["readiness_score"] = r.get("score")
            result["readiness_contributors"] = r.get("contributors")
            result["body_temperature_deviation"] = r.get("temperature_deviation")

        # --- Activity ---
        activity = _get("daily_activity", dp)
        a = first_for_date(activity)
        if a:
            result["activity_score"] = a.get("score")
            result["steps"] = a.get("steps")
            result["active_calories"] = a.get("active_calories")
            result["total_calories"] = a.get("total_calories")
            result["medium_activity_min"] = round((a.get("medium_activity_time") or 0) / 60)
            result["high_activity_min"] = round((a.get("high_activity_time") or 0) / 60)
            result["equivalent_walking_km"] = round((a.get("equivalent_walking_distance") or 0) / 1000, 1)

        # --- SpO2 ---
        spo2 = _get("daily_spo2", dp)
        sp = first_for_date(spo2)
        if sp:
            spo2_pct = sp.get("spo2_percentage") or {}
            avg = spo2_pct.get("average")
            result["spo2_avg"] = round(avg, 1) if avg is not None else None
            result["breathing_disturbance_index"] = sp.get("breathing_disturbance_index")

        # --- Meditation sessions ---
        sessions = _get("session", dp)
        if sessions and sessions.get("data"):
            meditations = [s for s in sessions["data"] if s.get("type") == "meditation" and s.get("day") == date]
            result["meditation_sessions"] = len(meditations)
            if meditations:
                total_sec = 0
                for m in meditations:
                    try:
                        start = datetime.fromisoformat(m["start_datetime"])
                        end = datetime.fromisoformat(m["end_datetime"])
                        total_sec += int((end - start).total_seconds())
                    except (KeyError, ValueError):
                        pass
                result["meditation_total_min"] = round(total_sec / 60)

        # --- Workouts ---
        workouts = _get("workout", dp)
        if workouts and workouts.get("data"):
            day_workouts = [w for w in workouts["data"] if w.get("day") == date]
            result["workouts"] = [
                {
                    "activity": w.get("activity"),
                    "intensity": w.get("intensity"),
                    "calories": w.get("calories"),
                    "start": w.get("start_datetime"),
                    "end": w.get("end_datetime"),
                }
                for w in day_workouts
            ]

        data_keys = [k for k, v in result.items() if v is not None and k not in ("date", "available")]
        if data_keys:
            result["available"] = True
            logger.info("Oura data fetched for %s | fields=%s", date, data_keys)
        else:
            result["error"] = "no data returned — token may be missing or Oura hasn't synced yet"
            logger.warning("Oura: no data fields populated for %s — token file present: %s", date, os.path.exists(OURA_TOKEN_FILE))

    except Exception as exc:
        logger.error("Oura data fetch error for %s: %s", date, exc, exc_info=True)
        result["error"] = str(exc)

    return result
