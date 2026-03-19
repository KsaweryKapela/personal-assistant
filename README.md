# Personal Assistant Bot

A self-hosted AI personal assistant on Telegram. Manages your calendar, tasks, activities, and habits — learns your preferences over time and checks in proactively every day.

---

## Features

- **Natural language** — talk to it like a person, no commands needed
- **Google Calendar** — create, edit, delete, and list events; auto-adds known contacts as attendees
- **Google Tasks** — create, list, and delete tasks
- **Activity & habit tracking** — logs workouts, work blocks, meals, and any custom activity
- **Daily summaries** — end-of-day stats: mood, energy, sleep, completion rate, highlights
- **User profile memory** — stores your preferences, contacts, health data, and goals; updates automatically as you share new info
- **Voice messages** — transcribes voice notes via Whisper and processes them like text
- **Proactive check-ins** — schedules follow-up messages after planned activities and evening reflections
- **Automated daily jobs** (all times configurable):
  - **07:00** — Morning check-in: wake time, mood, energy, plans
  - **23:30** — Profile review: reads the day's messages and updates your profile
  - **23:45** — Activity review: cross-references logged activities against conversation, fixes errors
  - **23:55** — Daily summary: computes day stats and sends an end-of-day report
- **RAG memory** — semantic search over past messages and activities via pgvector
- **Railway deployment** — webhook mode, PostgreSQL included, Google credentials injected from env vars

---

## Stack

| Layer | Tech |
|---|---|
| Bot interface | Telegram Bot API (webhook or polling) |
| AI | OpenAI (GPT) with tool-calling |
| Calendar & Tasks | Google Calendar API v3 + Google Tasks API |
| Voice | OpenAI Whisper |
| Database | PostgreSQL + pgvector (Railway) |
| Embeddings | `text-embedding-3-small` |
| Runtime | Python 3.11+ |
| Deployment | Railway (recommended) or local |

---

## Project structure

```
personal-assistant/
├── app/
│   ├── main.py              # Entry point — registers daily jobs, starts bot
│   ├── config.py            # All env-var loading with defaults
│   ├── assistant.py         # process_message orchestrator
│   ├── openai_client.py     # Agentic loop, tool definitions, system prompt
│   ├── calendar_client.py   # Google Calendar + Tasks CRUD
│   ├── database.py          # PostgreSQL layer (activities, messages, profile, summaries)
│   ├── profile_client.py    # User profile load/save with Railway env sync
│   ├── scheduler.py         # Background scheduler for proactive messages
│   ├── telegram_bot.py      # Telegram handler (text + voice)
│   ├── voice.py             # Whisper transcription
│   ├── log_bot.py           # Optional: stream logs to a second Telegram bot
│   └── utils.py             # send_telegram helper (with chunking), date formatting
├── start.sh                 # Railway entrypoint — writes credentials from env, then runs app
├── railway.toml             # Railway build + deploy config
├── .env.example             # Template for all environment variables
├── pyproject.toml
└── requirements.txt
```

---

## Deployment on Railway (recommended)

Railway gives you a free PostgreSQL database, persistent env vars, and automatic deploys on push.

### 1. Fork / clone the repo

### 2. Create a Railway project

1. Go to [railway.app](https://railway.app) and create a new project.
2. Add a **PostgreSQL** service to the project (Railway auto-injects `DATABASE_URL`).
3. Add a **new service** from your GitHub repo.

### 3. Set environment variables

In your Railway service → **Variables**, add all variables from `.env.example`. Required ones:

| Variable | Where to get it |
|---|---|
| `OPENAI_API_KEY` | [platform.openai.com](https://platform.openai.com) |
| `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather) on Telegram |
| `TELEGRAM_CHAT_ID` | Message [@userinfobot](https://t.me/userinfobot) on Telegram |
| `DATABASE_URL` | Auto-injected by Railway PostgreSQL service |
| `GOOGLE_CREDENTIALS_JSON` | Contents of `credentials.json` (see Google setup below) |
| `GOOGLE_TOKEN_JSON` | Contents of `token.json` after first OAuth login |
| `WEBHOOK_URL` | Your Railway service's public URL (e.g. `https://my-app.up.railway.app`) |
| `USER_PROFILE` | Single-line JSON with your initial profile (see `.env.example`) |

### 4. Google credentials on Railway

Railway has no persistent filesystem, so credentials are injected via env vars and written to disk by `start.sh` on each startup:

1. Complete the Google OAuth setup locally (see below) to get `credentials.json` and `token.json`.
2. Copy the full JSON contents of each file into Railway env vars:
   - `GOOGLE_CREDENTIALS_JSON` = contents of `credentials.json`
   - `GOOGLE_TOKEN_JSON` = contents of `token.json`
3. Set paths (optional — defaults already match `start.sh`):
   - `GOOGLE_CREDENTIALS_FILE=credentials.json`
   - `GOOGLE_TOKEN_FILE=token.json`

### 5. Deploy

Push to your connected branch. Railway builds and deploys automatically.

---

## Google setup

### A. Enable APIs

1. Go to [Google Cloud Console](https://console.cloud.google.com).
2. Create or select a project.
3. **APIs & Services → Library** → enable:
   - **Google Calendar API**
   - **Google Tasks API**

### B. Create OAuth 2.0 credentials

1. **APIs & Services → Credentials → Create Credentials → OAuth client ID**
2. Configure consent screen if prompted: External, add your email as test user.
3. Application type: **Desktop app**
4. Download the JSON → save as `credentials.json` in the project root.

### C. First-run authentication (local only)

```bash
python -m app.main
```

A browser window opens for Google login. After approving, `token.json` is saved automatically. Copy both files' contents into Railway env vars as described above.

> **Tip:** Publish your OAuth consent screen ("Publish app") to avoid the 7-day token expiry in Testing mode.

---

## Running locally

```bash
# 1. Install dependencies
pip install uv
uv venv && source .venv/bin/activate
uv pip install -e .

# 2. Set up env
cp .env.example .env
# Fill in .env

# 3. Run
python -m app.main
```

Leave `WEBHOOK_URL` unset for polling mode (no public URL needed locally).

---

## Environment variables

### Required

| Variable | Description |
|---|---|
| `OPENAI_API_KEY` | OpenAI API key |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token from BotFather |
| `TELEGRAM_CHAT_ID` | Your personal Telegram chat ID — gates all automated daily jobs |
| `DATABASE_URL` | PostgreSQL connection string (auto-injected by Railway) |
| `USER_PROFILE` | Initial user profile as single-line JSON (see `.env.example` for schema) |

### Google

| Variable | Default | Description |
|---|---|---|
| `GOOGLE_CREDENTIALS_FILE` | `credentials.json` | Path to OAuth client JSON |
| `GOOGLE_TOKEN_FILE` | `token.json` | Path where OAuth token is stored |
| `GOOGLE_CREDENTIALS_JSON` | — | Full JSON contents of credentials file (Railway only) |
| `GOOGLE_TOKEN_JSON` | — | Full JSON contents of token file (Railway only) |

### Bot & server

| Variable | Default | Description |
|---|---|---|
| `OPENAI_MODEL` | `gpt-4o` | OpenAI model to use |
| `TIMEZONE` | `Europe/Warsaw` | IANA timezone for all scheduling |
| `WEBHOOK_URL` | — | Public HTTPS URL for webhook mode (leave unset for polling) |
| `PORT` | `8080` | Port the webhook server listens on |

### Logging (optional)

| Variable | Description |
|---|---|
| `LOG_BOT_TOKEN` | Token of a second Telegram bot that receives all INFO+ logs |
| `LOG_CHAT_ID` | Chat ID where the log bot sends messages |

### Daily job times (optional)

All times are in 24h format in the configured `TIMEZONE`.

| Variable | Default | Description |
|---|---|---|
| `DAILY_MORNING_CHECK_TIME` | `05:00` | Morning check-in — morning routine, mood, plans |
| `DAILY_PROFILE_REVIEW_TIME` | `23:30` | Profile update from day's messages |
| `DAILY_ACTIVITY_REVIEW_TIME` | `23:45` | Activity log audit and corrections |
| `DAILY_SUMMARY_TIME` | `23:55` | End-of-day stats and report |

### Railway profile sync (optional)

Keeps `USER_PROFILE` env var in sync with the database so it survives a DB reset.

| Variable | Description |
|---|---|
| `RAILWAY_API_TOKEN` | Railway API token (Settings → Tokens) |
| `RAILWAY_PROJECT_ID` | Your Railway project ID |
| `RAILWAY_ENVIRONMENT_ID` | Your Railway environment ID |
| `RAILWAY_SERVICE_ID` | Your Railway service ID |

---

## What the bot can do

Tell it anything naturally:

| Input | What happens |
|---|---|
| "Set meeting with Adam tomorrow at 3pm" | Creates calendar event, adds Adam if contact is known |
| "Log my workout — 45 min strength training" | Logs activity with category=workout |
| "Add 'buy groceries' to my tasks" | Creates Google Task |
| "Show my tasks" | Lists pending tasks |
| "How have I been sleeping this week?" | Queries daily_summaries, reports sleep trends |
| "Show my profile" | Sends full profile JSON |
| "Remind me to stretch at 18:00" | Schedules a check-in for 18:00 |
| "Cancel my 18:00 check-in" | Cancels it by ID |
| "What did I do yesterday?" | Searches activity log and messages |

---

## Database tables

| Table | Contents |
|---|---|
| `activities` | Logged activities with status, notes, start/end time, embedding |
| `messages` | All conversation messages with embeddings for semantic search |
| `profile` | User profile JSON (single row per user) |
| `daily_summaries` | Per-day stats: sleep, mood, energy, scores, highlights |
