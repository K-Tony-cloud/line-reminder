import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.webhooks import MessageEvent

from .config import get_settings, Settings
from .scheduler import start_scheduler, stop_scheduler, _send_daily_summaries
from .line_bot import handle_message
from .models import CreateEventRequest, UpdateEventRequest
from . import sheets
from .backup import backup_events

# Resolve paths relative to this file so they work regardless of working dir
BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    try:
        sheets.ensure_sheet_exists()
        logger.info("Google Sheets connection verified")
    except Exception as e:
        logger.error("Google Sheets init failed: %s", e)
    if os.environ.get("DISABLE_SCHEDULER", "false").lower() != "true":
        start_scheduler()
    else:
        logger.info("Scheduler disabled — worker service handles reminders")
    yield
    stop_scheduler()


app = FastAPI(title="LINE Event Reminder", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def get_webhook_handler(settings: Settings = Depends(get_settings)) -> WebhookHandler:
    return WebhookHandler(settings.line_channel_secret)


# ─── LINE Webhook ───────────────────────────────────────────────────────────

@app.post("/webhook")
async def webhook(request: Request, settings: Settings = Depends(get_settings)):
    signature = request.headers.get("X-Line-Signature", "")
    body = await request.body()

    handler = WebhookHandler(settings.line_channel_secret)

    @handler.add(MessageEvent)
    def on_message(event):
        handle_message(event)

    try:
        handler.handle(body.decode("utf-8"), signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    return {"status": "ok"}


# ─── Dashboard UI ────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


# ─── REST API for Dashboard ──────────────────────────────────────────────────

@app.get("/api/events")
async def list_events(status: str | None = None, user_id: str | None = None):
    events = sheets.get_all_events()
    if status:
        events = [e for e in events if e.status.value == status]
    if user_id:
        events = [e for e in events if e.user_id == user_id]
    events.sort(key=lambda e: (e.event_date, e.event_time))
    return events


@app.post("/api/events", status_code=201)
async def create_event(req: CreateEventRequest):
    return sheets.create_event(req)


@app.patch("/api/events/{event_id}")
async def update_event(event_id: str, req: UpdateEventRequest):
    updated = sheets.update_event(event_id.upper(), req)
    if not updated:
        raise HTTPException(status_code=404, detail="Event not found")
    return updated


@app.delete("/api/events/{event_id}", status_code=204)
async def delete_event(event_id: str):
    if not sheets.delete_event(event_id.upper()):
        raise HTTPException(status_code=404, detail="Event not found")


@app.post("/api/daily-summary")
async def trigger_daily_summary():
    await _send_daily_summaries()
    return {"status": "ok"}


@app.post("/api/backup")
async def trigger_backup():
    try:
        return backup_events()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/health")
async def health():
    return {"status": "ok"}
