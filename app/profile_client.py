import logging
import os

import requests as http_requests

logger = logging.getLogger(__name__)

_RAILWAY_GQL = "https://backboard.railway.app/graphql/v2"

_DEFAULT_PROFILE = """Name: Ksawery Kapela
Age: 27
Location: Kraków, Poland
Profession: GenAI / ML engineer and developer (Python, cloud systems).
Work style: Deep focus, minimal meetings, fast iterations, MVP-first. Prefers simple architectures.
Stack: Python, LangChain, Transformers, TensorFlow/PyTorch, AWS.

Health & fitness: Gym 3–4x/week, strength training. Weight ~82 kg, height 185 cm.
Diet: High-protein — eggs, steak, skyr, cottage cheese, protein shakes. Simple, quick meals.

Lifestyle: Walks, reading, music, gym, psychology, AI/tech, productivity, self-development.
Relationship: Has a girlfriend, Wiktoria.

Personality: Ambitious, analytical, pragmatic, creative, open-minded.
Motto: "I will not live the life that I do not deserve."

Contacts:
- Wiktoria Siemaszko (girlfriend / Wika): wiktoria.siemaszko2503@gmail.com
  Always invite her to calendar events that involve her.

Assistant preferences: Help structure the day, schedule tasks, suggest learning resources,
plan workouts, maintain healthy habits, optimise productivity. Be concise, direct, practical."""


def load_profile() -> str:
    """Return the current user profile. Reads from env var, falls back to default."""
    return os.getenv("USER_PROFILE") or _DEFAULT_PROFILE


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
