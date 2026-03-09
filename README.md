# Personal Assistant Bot

A minimal Telegram bot that understands natural-language messages and creates Google Calendar events using OpenAI.

Send "Set meeting with Adam tomorrow at 3pm" → event appears in your Google Calendar, bot replies with confirmation.

---

## Stack

| Layer | Tech |
|---|---|
| Bot interface | Telegram Bot API (long-polling) |
| Intelligence | OpenAI `gpt-4o` with tool-calling |
| Calendar | Google Calendar API v3 |
| Runtime | Python 3.11+, FastAPI-free (polling needs no server) |
| Config | python-dotenv |

**Why polling instead of webhooks?**
Polling requires zero infrastructure — no public URL, no tunnel, no server config. For a single-user personal assistant running locally (or on a small VPS) it is the simplest and most reliable choice.

---

## Project structure

```
personal-assistant/
├── app/
│   ├── main.py            # Entry point, starts polling
│   ├── config.py          # Env-var loading
│   ├── schemas.py         # Pydantic models
│   ├── openai_client.py   # Intent extraction via tool-calling
│   ├── calendar_client.py # Google Calendar CRUD
│   ├── assistant.py       # Orchestrator (the main logic)
│   ├── telegram_bot.py    # Telegram handler
│   └── utils.py           # Date formatting helpers
├── .env.example
├── .gitignore
├── pyproject.toml
└── requirements.txt
```

---

## Setup

### 1. Clone / download the project

```bash
git clone <repo>
cd personal-assistant
```

### 2. Install dependencies

**With uv (recommended):**
```bash
pip install uv          # one-time, if uv is not installed
uv venv
source .venv/bin/activate
uv pip install -e .
```

**With pip:**
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Create your `.env` file

```bash
cp .env.example .env
```

Fill in all values (see details below).

---

## Telegram bot setup

1. Open Telegram and start a chat with [@BotFather](https://t.me/BotFather).
2. Send `/newbot` and follow the prompts.
3. Copy the token BotFather gives you.
4. Add it to `.env`:
   ```
   TELEGRAM_BOT_TOKEN=123456789:ABC-your-token-here
   ```
5. Start a conversation with your new bot (search its username in Telegram) so it can send you messages.

---

## Google Calendar setup

### A. Create a Google Cloud project & enable the Calendar API

1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project (or select an existing one).
3. Navigate to **APIs & Services → Library**.
4. Search for **Google Calendar API** and click **Enable**.

### B. Create OAuth 2.0 credentials

1. Go to **APIs & Services → Credentials**.
2. Click **Create Credentials → OAuth client ID**.
3. If prompted, configure the **OAuth consent screen** first:
   - Choose **External** (or Internal if using Google Workspace).
   - Fill in App name (anything), support email, developer email.
   - On the **Scopes** step you can skip — the app requests them at runtime.
   - On the **Test users** step, add your own Google email address.
4. Back in **Create Credentials → OAuth client ID**:
   - Application type: **Desktop app**
   - Name: anything (e.g. "Personal Assistant")
5. Click **Create**, then **Download JSON**.
6. Save the file as `credentials.json` in the project root.
7. Set in `.env`:
   ```
   GOOGLE_CREDENTIALS_FILE=credentials.json
   ```

### C. First-run authentication

The first time you run the bot, a browser window will open asking you to log in to Google and grant calendar access. After you approve:
- A `token.json` file is saved automatically.
- All future runs use this token silently (it auto-refreshes).

> **Note:** If your OAuth app is in "Testing" mode, the token expires after 7 days and you'll need to re-authenticate. To avoid this, publish the app (click "Publish app" in the consent screen — for a personal app this is fine).

---

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `OPENAI_API_KEY` | yes | — | OpenAI API key |
| `TELEGRAM_BOT_TOKEN` | yes | — | Token from BotFather |
| `GOOGLE_CREDENTIALS_FILE` | no | `credentials.json` | Path to OAuth client JSON |
| `GOOGLE_TOKEN_FILE` | no | `token.json` | Path where token is stored |
| `TIMEZONE` | no | `Europe/Warsaw` | IANA timezone for calendar events |
| `OPENAI_MODEL` | no | `gpt-4o` | OpenAI model to use |

---

## Running locally

```bash
# Make sure .venv is active and .env is filled in
python -m app.main
```

You should see:
```
2024-03-08 12:00:00  INFO      app.main  Starting personal assistant bot (polling mode)...
```

Send a message to your Telegram bot. Logs appear in the terminal.

Stop with `Ctrl+C`.

---

## Example interactions

| You send | Bot replies |
|---|---|
| `Set meeting with Adam tomorrow at 3pm` | `Done — added 'Meeting with Adam' on Friday, March 9 at 15:00.` |
| `Book gym on Friday at 18:00 for 1 hour` | `Done — added 'Gym' on Friday, March 9 at 18:00.` |
| `Schedule lunch with Wiktoria` | `What day and time should I schedule it?` |
| `Remind me about call with client tomorrow` | `What time should I schedule the call?` |
| `Hey` | `Hey! I can help you schedule events — just tell me what to add to your calendar.` |
| `Add a 30 minute meeting with Tom at 4pm` | `What day should I schedule it?` |

---

## Example `.env`

```dotenv
OPENAI_API_KEY=sk-proj-...
TELEGRAM_BOT_TOKEN=7123456789:AAF...
GOOGLE_CREDENTIALS_FILE=credentials.json
GOOGLE_TOKEN_FILE=token.json
TIMEZONE=Europe/Warsaw
OPENAI_MODEL=gpt-4o
```

---

## Known MVP limitations

- **Single user only.** The bot replies to anyone who messages it. If you want privacy, add a `ALLOWED_CHAT_ID` check in `telegram_bot.py`.
- **No conversation memory.** Each message is processed independently — the bot cannot refer back to previous turns.
- **No event listing.** "What do I have tomorrow?" is not implemented.
- **No event editing or deletion.**
- **No recurring events.**
- **OAuth re-auth needed every ~7 days** if the consent screen is in Testing mode (see Google setup above).
- **Blocking Google/OpenAI calls** run in a thread pool via `asyncio.to_thread` — fine for single-user, not suited for high concurrency.
