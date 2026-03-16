import os
from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise RuntimeError(f"Missing required environment variable: {key}")
    return val


OPENAI_API_KEY: str = _require("OPENAI_API_KEY")
TELEGRAM_BOT_TOKEN: str = _require("TELEGRAM_BOT_TOKEN")
GOOGLE_CREDENTIALS_FILE: str = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
GOOGLE_TOKEN_FILE: str = os.getenv("GOOGLE_TOKEN_FILE", "token.json")
TIMEZONE: str = os.getenv("TIMEZONE", "Europe/Warsaw")
OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o")
WEBHOOK_URL: str | None = os.getenv("WEBHOOK_URL")  # e.g. https://my-app.railway.app
PORT: int = int(os.getenv("PORT", "8080"))

# Optional — Telegram bot dedicated to streaming logs
LOG_BOT_TOKEN: str | None = os.getenv("LOG_BOT_TOKEN")
LOG_CHAT_ID: str | None = os.getenv("LOG_CHAT_ID")

# Your personal Telegram chat ID — used for recurring automated tasks (daily profile review etc.)
# Find it by messaging @userinfobot on Telegram.
_chat_id_raw = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_CHAT_ID: int | None = int(_chat_id_raw) if _chat_id_raw else None

# PostgreSQL — auto-injected by Railway when a Postgres service is in the same project
DATABASE_URL: str = _require("DATABASE_URL")
