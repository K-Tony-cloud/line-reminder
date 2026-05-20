# LINE Event Reminder System

A FastAPI backend that lets LINE users schedule event reminders stored in Google Sheets, with a web dashboard to manage everything.

## Architecture

```
LINE User ──► LINE Webhook ──► FastAPI ──► Google Sheets
                                  │
                          APScheduler (every min)
                                  │
                          LINE Push API ──► User
```

## Setup (15 minutes)

### 1. Clone & install

```bash
cd line-reminder
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### 2. Google Sheets service account

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a project → **APIs & Services** → Enable **Google Sheets API**
3. **IAM & Admin → Service Accounts** → Create service account → Create JSON key
4. Save the JSON file as `credentials/service_account.json`
5. Create a new Google Sheet, copy its ID from the URL
6. Share the sheet with the service account email (Editor role)
7. Add the spreadsheet ID to `.env`

### 3. LINE Bot

1. Go to [LINE Developers Console](https://developers.line.biz/console/)
2. Create a new provider → Create a **Messaging API** channel
3. Copy **Channel Access Token** and **Channel Secret** → add to `.env`
4. Set webhook URL (see step 4 for ngrok URL): `https://<your-domain>/webhook`
5. Enable **Use webhook** and disable **Auto-reply messages**

### 4. Start the server

```bash
# Terminal 1: run the app
python run.py

# Terminal 2: expose to LINE via ngrok
ngrok http 8000
```

Copy the ngrok HTTPS URL and set it as the webhook URL in LINE Developers Console.

### 5. Test

1. Open `http://localhost:8000` → web dashboard
2. Add your LINE Bot as a friend
3. Send `/help` in the LINE chat

---

## LINE Bot Commands

| Command | Description |
|---------|-------------|
| `/add <name> <YYYY-MM-DD> <HH:MM> [remind=60]` | Add an event |
| `/list` | Show your active events |
| `/delete <ID>` | Cancel an event |
| `/help` | Show help |

**Example:**
```
/add Team Standup 2026-05-15 09:00 remind=15
```

---

## Dashboard Features

- View all events with status filter and search
- Add events manually (useful for adding reminders for any LINE user ID)
- Edit event name, date, time, or reminder window
- Cancel events
- Auto-refreshes every 60 seconds

---

## Google Sheets Schema

Sheet name: `Events`

| Column | Description |
|--------|-------------|
| ID | 8-char unique ID |
| UserID | LINE user ID |
| DisplayName | LINE display name |
| EventName | Event title |
| EventDate | YYYY-MM-DD |
| EventTime | HH:MM |
| ReminderMinutes | Minutes before event to notify |
| Status | active / sent / cancelled |
| CreatedAt | Timestamp |

---

## Deployment — Railway

### 1. Push to GitHub

```bash
git init
git add -A
git commit -m "Initial commit"
git remote add origin https://github.com/<you>/line-reminder.git
git push -u origin main
```

### 2. Create Railway project

1. Go to **railway.app** → **New Project** → **Deploy from GitHub repo**
2. Select your repository
3. Railway auto-detects Python via `requirements.txt` and starts building

### 3. Set environment variables

In Railway dashboard → your service → **Variables** tab, add:

| Variable | Value |
|----------|-------|
| `LINE_CHANNEL_ACCESS_TOKEN` | From LINE Developers Console |
| `LINE_CHANNEL_SECRET` | From LINE Developers Console |
| `GOOGLE_SPREADSHEET_ID` | From your Google Sheet URL |
| `GOOGLE_CREDENTIALS_JSON` | Full JSON content of `credentials/service_account.json` |
| `DEFAULT_REMINDER_MINUTES` | `60` (optional) |

> **`GOOGLE_CREDENTIALS_JSON`**: Open `credentials/service_account.json`, copy the entire
> file contents, and paste it as a single line in the Railway variable field.
> Do **not** add `GOOGLE_CREDENTIALS_FILE` — it is only used locally.

`PORT` is injected automatically by Railway — do not set it.

### 4. Get your Railway domain

1. Railway dashboard → your service → **Settings** → **Networking**
2. Click **Generate Domain** → copy the URL (e.g. `https://line-reminder-production.up.railway.app`)

### 5. Update LINE webhook

In **LINE Developers Console** → your channel → **Messaging API** tab:

- Webhook URL: `https://<your-railway-domain>/webhook`
- Enable **Use webhook** ✓
- Disable **Auto-reply messages** ✓

### 6. Done

- Dashboard: `https://<your-railway-domain>/`
- Bot: send `/help` in LINE

### Notes

- Railway restarts the container on crash (configured in `railway.json`)
- The scheduler runs inside the same process — Railway's single-instance deployment means no duplicate reminders
- Logs are available in Railway dashboard → **Deployments** → **View Logs**
