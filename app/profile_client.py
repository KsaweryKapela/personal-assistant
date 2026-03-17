import json
import logging
import os
import time

import requests as http_requests

logger = logging.getLogger(__name__)

_RAILWAY_GQL = "https://backboard.railway.app/graphql/v2"


def load_profile(chat_id: int = 0) -> dict:
    """Load profile from DB (primary). Falls back to USER_PROFILE env if DB is missing or empty."""
    from app.database import load_profile_from_db, save_profile_to_db

    profile = load_profile_from_db(chat_id)

    # DB has real data — use it
    if profile:
        logger.info("load_profile | source=db | chat_id=%s | categories=%s", chat_id, list(profile.keys()))
        return profile

    # DB is missing (None) or empty ({}) — try env var fallback
    raw = os.getenv("USER_PROFILE")
    env_profile = json.loads(raw) if raw else None

    if env_profile:
        # Env has data: save it to DB (first-run migration or recovery from data loss)
        save_profile_to_db(chat_id, env_profile)
        logger.info(
            "load_profile | source=env (%s) | chat_id=%s | categories=%s",
            "migrated" if profile is None else "recovered",
            chat_id, list(env_profile.keys()),
        )
        return env_profile

    # Both DB and env are empty/missing
    if profile is not None:
        # DB had {} — genuinely empty profile, return it
        logger.info("load_profile | source=db (empty) | chat_id=%s", chat_id)
        return profile

    raise RuntimeError("Missing required environment variable: USER_PROFILE (and no DB record found)")


def save_profile(profile: dict, chat_id: int = 0) -> dict:
    """Save profile to DB (primary) and sync to Railway env (backup)."""
    from app.database import save_profile_to_db
    save_profile_to_db(chat_id, profile)

    # Keep Railway env in sync as a backup
    new_profile_str = json.dumps(profile, ensure_ascii=False)
    os.environ["USER_PROFILE"] = new_profile_str

    api_token = os.getenv("RAILWAY_API_TOKEN")
    project_id = os.getenv("RAILWAY_PROJECT_ID")
    environment_id = os.getenv("RAILWAY_ENVIRONMENT_ID")
    service_id = os.getenv("RAILWAY_SERVICE_ID")

    if not all([api_token, project_id, environment_id, service_id]):
        logger.info("save_profile | Railway env sync skipped (not configured)")
        return {"ok": True}

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
                "name": "USER_PROFILE",
                "value": new_profile_str,
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
        logger.info("save_profile | Railway env sync ok | duration=%.2fs", time.monotonic() - t0)
        return {"ok": True}
    except Exception as exc:
        logger.warning("save_profile | Railway env sync failed | duration=%.2fs | error=%s", time.monotonic() - t0, exc)
        return {"ok": True, "warning": f"Saved to DB but Railway env sync failed: {exc}"}
