# Personal Assistant Bot

A self-hosted AI personal assistant on Telegram. Talks to you like a person, manages your Google Calendar and Tasks, tracks habits and activities, learns your preferences over time, and checks in proactively throughout the day.

---

## Features

### Calendar & Events
- Create, edit, delete, and list Google Calendar events in natural language
- Recurring events — daily, weekly, weekdays, monthly, yearly with custom end dates or counts
- Event color-coding by type (workout → flamingo, work/meeting → blueberry, meal → banana, social → grape, health → tomato, personal/habit → sage, travel → peacock)
- Auto-adds known contacts from your profile as attendees when you mention them

### Tasks
- Create, complete, update, and delete Google Tasks (visible in Google Calendar)
- **Task series** — create the same task for N consecutive days with one command; cancel the whole series by series ID
- Duplicate detection — checks for existing tasks before creating new ones

### Activity & Habit Tracking
- Log any activity: workouts, work blocks, meals, reading, walks, social, or custom categories
- Track status: completed, completed\_late, skipped, or partial
- Record start/end times and freeform notes per activity
- Correct or delete past entries
- Query completion rates and trends by category over any time period

### Daily Automated Jobs
All times are configurable and run in your local timezone.

| Time (default) | Job | What it does |
|---|---|---|
| `05:00` | Morning check-in | Asks wake time, mood, energy, and today's plans. Logs wake-up activity. |
| `23:30` | Profile review | Reads the day's conversation, updates your profile with new insights and patterns. |
| `23:45` | Activity review | Cross-references logged activities against the conversation; fixes errors, fills gaps, logs completed tasks as activities. |
| `23:55` | Daily summary | Computes full-day stats — mood, energy, stress, completion rate, highlights, sleep — and sends a report. |

### Day Wrap-Up
Say "let's wrap up the day" and the bot walks through every calendar event and task from the day, asks what happened for anything not already covered, and logs it all.

### Proactive Check-Ins
- Schedules context-aware follow-up messages after planned activities (gym, deep work, meetings)
- Evening reflection around 20:00
- Triggered by "remind me at…", "check back in 2 hours", etc.
- At send time, the AI recomposes the message with fresh calendar and activity context — not a static reminder

### Semantic Memory (RAG)
- Every message and activity is embedded with `text-embedding-3-small` and stored in pgvector
- `search_memory` retrieves semantically relevant past messages and activities for any question
- Used automatically when you ask about past mood, habits, progress, or patterns

### User Profile
Structured personal profile the bot reads and updates continuously:

| Category | Examples |
|---|---|
| Personal | name, age, location |
| Career | profession, tech stack, work style |
| Health | height, weight, conditions |
| Diet | preferences, restrictions |
| Lifestyle | hobbies, interests |
| Relationships | contacts with email addresses |
| Personality | traits, communication preferences |
| Assistant preferences | tone, check-in frequency |

Profile changes are synced to Railway env vars as a backup so they survive a DB reset.

### Voice Messages
Record a voice note in Telegram — the bot transcribes it with Whisper and processes it identically to text.

### Daily Summaries & Analytics
End-of-day report includes:
- Activity completion rate (completed / total scheduled)
- Mood, energy, stress, gut, relationship scores (1–10)
- Sleep duration and quality
- Highlights, challenges, key takeaways
- Stored per day and queryable over any range

---

## How It Works

Each message triggers an agentic loop:

1. The bot loads today's calendar events, tasks, pending check-ins, recent activities, and your profile as context
2. This is sent to GPT-4o alongside the conversation history and a full tool list
3. The model calls tools (create\_event, log\_activity, search\_memory, etc.) until it has everything it needs
4. It sends one final reply

The system prompt is structured so static instructions come before dynamic context, maximising OpenAI prompt cache hits — the instruction block is cached for the entire day and reused across every request.

---

## Stack

| Layer | Tech |
|---|---|
| Bot interface | Telegram Bot API (webhook or polling) |
| AI | OpenAI GPT-4o with tool-calling agentic loop |
| Calendar & Tasks | Google Calendar API v3 + Google Tasks API |
| Voice | OpenAI Whisper |
| Embeddings | `text-embedding-3-small` (1536 dims) |
| Database | PostgreSQL + pgvector |
| Runtime | Python 3.11+ |
| Deployment | Railway (recommended) or local |

---

## Project Structure

```
personal-assistant/
├── app/
│   ├── main.py              # Entry point — registers daily jobs, starts bot
│   ├── config.py            # All env-var loading with defaults
│   ├── assistant.py         # process_message orchestrator
│   ├── openai_client.py     # Agentic loop, tool definitions, system prompt
│   ├── calendar_client.py   # Google Calendar + Tasks CRUD + task series
│   ├── database.py          # PostgreSQL layer (activities, messages, profile, summaries)
│   ├── profile_client.py    # User profile load/save with Railway env sync
│   ├── scheduler.py         # Background scheduler for proactive messages
│   ├── telegram_bot.py      # Telegram handler (text + voice)
│   ├── voice.py             # Whisper transcription
│   ├── log_bot.py           # Optional: stream logs to a second Telegram bot
│   └── utils.py             # send_telegram (with 4000-char chunking), date formatting
├── start.sh                 # Railway entrypoint — writes credentials from env, then runs app
├── railway.toml             # Railway build + deploy config
├── .env.example             # Template for all environment variables
├── pyproject.toml
└── requirements.txt
```

---

## Deployment on Railway (recommended)

Railway gives you a PostgreSQL database, persistent env vars, and automatic deploys on push.

### 1. Fork / clone the repo

### 2. Create a Railway project

1. Go to [railway.app](https://railway.app) and create a new project.
2. Add a **PostgreSQL** service — Railway auto-injects `DATABASE_URL`.
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
| `USER_PROFILE` | Your initial profile as single-line JSON (see `.env.example` for schema) |

### 4. Google credentials on Railway

Railway has no persistent filesystem, so credentials are injected via env vars and written to disk by `start.sh` on each startup.

1. Complete Google OAuth locally first (see below) to get `credentials.json` and `token.json`.
2. Copy the full JSON contents of each file into Railway env vars:
   - `GOOGLE_CREDENTIALS_JSON` = contents of `credentials.json`
   - `GOOGLE_TOKEN_JSON` = contents of `token.json`

### 5. Deploy

Push to your connected branch. Railway builds and deploys automatically.

---

## Google Setup

### A. Enable APIs

1. Go to [Google Cloud Console](https://console.cloud.google.com).
2. Create or select a project.
3. **APIs & Services → Library** → enable:
   - **Google Calendar API**
   - **Google Tasks API**

### B. Create OAuth 2.0 credentials

1. **APIs & Services → Credentials → Create Credentials → OAuth client ID**
2. Configure consent screen if prompted: External, add your email as a test user.
3. Application type: **Desktop app**
4. Download the JSON → save as `credentials.json` in the project root.

### C. First-run authentication (local only)

```bash
python -m app.main
```

A browser window opens for Google login. After approving, `token.json` is saved automatically. Copy both files' contents into Railway env vars as described above.

> **Tip:** Publish your OAuth consent screen ("Publish app") to avoid the 7-day token expiry that applies in Testing mode.

---

## Running Locally

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

Leave `WEBHOOK_URL` unset for polling mode — no public URL needed.

---

## Environment Variables

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

### Bot & Server

| Variable | Default | Description |
|---|---|---|
| `OPENAI_MODEL` | `gpt-4o` | OpenAI model to use |
| `TIMEZONE` | `Europe/Warsaw` | IANA timezone for all scheduling |
| `WEBHOOK_URL` | — | Public HTTPS URL for webhook mode (leave unset for polling) |
| `PORT` | `8080` | Port the webhook server listens on |

### Daily Job Times (optional)

All times are in 24h format in the configured `TIMEZONE`.

| Variable | Default | Description |
|---|---|---|
| `DAILY_MORNING_CHECK_TIME` | `05:00` | Morning check-in |
| `DAILY_PROFILE_REVIEW_TIME` | `23:30` | Profile update from day's messages |
| `DAILY_ACTIVITY_REVIEW_TIME` | `23:45` | Activity log audit and corrections |
| `DAILY_SUMMARY_TIME` | `23:55` | End-of-day stats and report |

### Logging (optional)

| Variable | Description |
|---|---|
| `LOG_BOT_TOKEN` | Token of a second Telegram bot that receives all INFO+ logs |
| `LOG_CHAT_ID` | Chat ID where the log bot sends messages |

### Railway Profile Sync (optional)

Keeps `USER_PROFILE` env var in sync with the database so it survives a DB reset.

| Variable | Description |
|---|---|
| `RAILWAY_API_TOKEN` | Railway API token (Settings → Tokens) |
| `RAILWAY_PROJECT_ID` | Your Railway project ID |
| `RAILWAY_ENVIRONMENT_ID` | Your Railway environment ID |
| `RAILWAY_SERVICE_ID` | Your Railway service ID |

---

## Example Interactions

| You say | What happens |
|---|---|
| "Schedule a meeting with Adam tomorrow at 3pm for 1 hour" | Creates calendar event, auto-adds Adam's email if he's in your contacts |
| "Add a recurring gym session every Monday and Thursday at 7am" | Creates a weekly recurring event on MO + TH |
| "Meditate every day for the next 14 days" | Creates a 14-task series with a shared series ID |
| "Delete my meditation series" | Deletes all tasks in the series at once |
| "Log my workout — 45 min strength training, felt strong" | Logs activity: category=workout, status=completed, with notes |
| "I skipped my run today" | Logs activity: category=workout, status=skipped |
| "How have I been sleeping this week?" | Queries daily summaries, reports sleep trends |
| "What did I work on yesterday?" | Searches activity log and conversation history semantically |
| "Remind me to check my email at 14:00" | Schedules a context-aware check-in for 14:00 |
| "Let's wrap up the day" | Reviews every event and task, asks for missing activity logs |
| "Show my profile" | Sends full profile JSON formatted as a Telegram message |

---

## Database Tables

| Table | Contents |
|---|---|
| `activities` | Logged activities with category, status, notes, start/end time, embedding |
| `messages` | All conversation messages with embeddings for semantic search |
| `profile` | User profile JSON (one row per user) |
| `daily_summaries` | Per-day stats: sleep, mood, energy, stress, scores, highlights, takeaways |
