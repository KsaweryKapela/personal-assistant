# Personal Assistant Bot

A Telegram bot that acts as a personal assistant — it understands natural language, manages your Google Calendar, remembers who you are, and learns new information about you over time.

Send "Schedule gym tomorrow at 7am" → event appears in Google Calendar.
Say "I also like cycling" → the bot updates your profile and remembers it.

---

## Features

- **Calendar management** — create, update, delete, and list events via natural language
- **User profile & memory** — persistent profile injected into every prompt; the bot updates it when you share new info
- **Contact awareness** — known contacts (with emails) are auto-invited to relevant events
- **Conversation memory** — last 20 messages per chat kept in-session so the bot has context
- **Webhook mode** — production-ready deployment on Railway (no polling conflicts)
- **Polling fallback** — no config needed for local development

---

## Stack

| Layer | Tech |
|---|---|
| Bot interface | Telegram Bot API (webhook / polling) |
| Intelligence | OpenAI with tool-calling (agentic loop) |
| Calendar | Google Calendar API v3 |
| Runtime | Python 3.11+ |
| Deployment | Railway (nixpacks) |

---

## Project structure

```
personal-assistant/
├── app/
│   ├── main.py            # Entry point — webhook or polling based on env
│   ├── config.py          # Env-var loading
│   ├── telegram_bot.py    # Telegram handler
│   ├── assistant.py       # Orchestrator
│   ├── openai_client.py   # Agentic loop with tool-calling
│   ├── calendar_client.py # Google Calendar CRUD + Railway token sync
│   ├── profile_client.py  # User profile load/save + Railway sync
│   └── utils.py           # Helpers
├── start.sh               # Railway entrypoint (writes creds, starts bot)
├── railway.toml
├── .env.example
├── pyproject.toml
└── requirements.txt
```

---

## Setup

### 1. Clone the repo

```bash
git clone <repo>
cd personal-assistant
```

### 2. Install dependencies

**With uv (recommended):**
```bash
pip install uv
uv venv && source .venv/bin/activate
uv pip install -e .
```

**With pip:**
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Create your `.env`

```bash
cp .env.example .env
```

Fill in all values — see the **Environment variables** section below.

---

## Telegram bot setup

1. Start a chat with [@BotFather](https://t.me/BotFather) and send `/newbot`.
2. Copy the token and set `TELEGRAM_BOT_TOKEN` in `.env`.
3. Start a conversation with your bot in Telegram so it can message you.

---

## Google Calendar setup

### A. Enable the API

1. Open [Google Cloud Console](https://console.cloud.google.com/) and create a project.
2. Go to **APIs & Services → Library**, search **Google Calendar API**, click **Enable**.

### B. Create OAuth credentials

1. Go to **APIs & Services → Credentials → Create Credentials → OAuth client ID**.
2. Configure the consent screen if prompted (External, add your email as a test user).
3. Application type: **Desktop app**.
4. Download the JSON and save as `credentials.json` in the project root.

### C. First-run authentication

On first run a browser opens for Google login. After approving:
- `token.json` is saved automatically and auto-refreshes on expiry.
- On Railway, the refreshed token is synced back to the `GOOGLE_TOKEN_JSON` env var so it survives redeployments.

> If your OAuth app stays in **Testing** mode, tokens expire after 7 days. To avoid this, click **Publish app** in the consent screen.

---

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `OPENAI_API_KEY` | yes | — | OpenAI API key |
| `TELEGRAM_BOT_TOKEN` | yes | — | Token from BotFather |
| `USER_PROFILE` | yes | — | Your personal profile text (see below) |
| `GOOGLE_CREDENTIALS_FILE` | no | `credentials.json` | Path to OAuth client JSON |
| `GOOGLE_TOKEN_FILE` | no | `token.json` | Path where token is stored |
| `GOOGLE_CREDENTIALS_JSON` | no | — | Full credentials JSON as string (Railway) |
| `GOOGLE_TOKEN_JSON` | no | — | Full token JSON as string (Railway) |
| `TIMEZONE` | no | `Europe/Warsaw` | IANA timezone for calendar events |
| `OPENAI_MODEL` | no | `gpt-4o` | OpenAI model to use |
| `WEBHOOK_URL` | no | — | Public HTTPS URL — enables webhook mode |
| `PORT` | no | `8080` | Port for the webhook server |
| `RAILWAY_API_TOKEN` | no | — | For syncing profile/token back to Railway vars |
| `RAILWAY_PROJECT_ID` | no | — | Railway project ID |
| `RAILWAY_ENVIRONMENT_ID` | no | — | Railway environment ID |
| `RAILWAY_SERVICE_ID` | no | — | Railway service ID |

### USER_PROFILE

The bot reads your profile on every message and uses it to personalise responses and auto-invite contacts. It also updates the profile when you share new information.

Set it as a multiline string in `.env`:

```dotenv
USER_PROFILE="Name: Your Name
Age: 30
Location: ...

Contacts:
- Person Name: email@example.com

Assistant preferences: ..."
```

On Railway, add it as an env var in the dashboard. The bot will keep it updated automatically via the Railway API.

---

## Running locally

```bash
# .venv active, .env filled in
python -m app.main
```

No `WEBHOOK_URL` set → polling mode. Send a message to your bot and watch the logs.

---

## Deploying to Railway

1. Push the repo to GitHub and connect it to a Railway service.
2. Set all required env vars in the Railway dashboard (including `USER_PROFILE`).
3. Set `WEBHOOK_URL` to your Railway public domain, e.g. `https://my-app.up.railway.app`.
4. Set `PORT` to `8080` (Railway target port).
5. Set the four `RAILWAY_*` vars if you want profile and token updates to persist across redeployments.
6. Deploy — Railway runs `bash start.sh` which writes credentials and starts the bot.

---

## Example interactions

| You send | What happens |
|---|---|
| `What's in my calendar today?` | Lists today's events with times |
| `Schedule gym tomorrow at 7am for 1 hour` | Creates the event |
| `Add a meeting with Wiktoria on Friday at 2pm` | Creates event and auto-invites her email |
| `Move my 3pm call to 4pm` | Updates the event |
| `Delete the gym session` | Deletes it after finding the event ID |
| `I also enjoy cycling` | Updates your profile to include cycling |
| `Add contact: Tom, tom@example.com` | Saves Tom's email to your profile |

---

## Known limitations

- **Single user.** No access control — anyone who finds your bot can message it. Add a `ALLOWED_CHAT_ID` check in `telegram_bot.py` if needed.
- **In-memory conversation history.** Restarting the bot clears the session history.
- **No recurring events.**
- **Blocking I/O** runs in a thread pool via `asyncio.to_thread` — fine for single-user use.
