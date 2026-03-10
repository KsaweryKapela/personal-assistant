import logging
import os

import requests as http_requests

logger = logging.getLogger(__name__)

_RAILWAY_GQL = "https://backboard.railway.app/graphql/v2"


def load_profile() -> str:
    """Return the current user profile from the USER_PROFILE env var."""
    profile = os.getenv("USER_PROFILE")
    if not profile:
        raise RuntimeError("Missing required environment variable: USER_PROFILE")
    return profile


def save_profile(new_profile: str) -> dict:
    """Persist the updated profile to file and Railway env var."""
    # Update in-process env so the next load_profile() call in this session reflects the change
    os.environ["USER_PROFILE"] = new_profile

    api_token = os.getenv("RAILWAY_API_TOKEN")
    project_id = os.getenv("RAILWAY_PROJECT_ID")
    environment_id = os.getenv("RAILWAY_ENVIRONMENT_ID")
    service_id = os.getenv("RAILWAY_SERVICE_ID")

    if not all([api_token, project_id, environment_id, service_id]):
        logger.info("Profile updated in memory (Railway sync not configured).")
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
                "value": new_profile,
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
        logger.info("Synced updated profile to Railway env vars.")
        return {"ok": True}
    except Exception as exc:
        logger.warning("Could not sync profile to Railway: %s", exc)
        return {"ok": True, "warning": f"Saved in memory but Railway sync failed: {exc}"}
