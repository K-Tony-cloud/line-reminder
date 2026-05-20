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
from .scheduler import start_scheduler, stop_scheduler, send_daily_summary, sync_event_reminders, schedule_event_reminder
from .line_bot import handle_message
from .models import CreateEventRequest, UpdateEventRequest
from . import sheets
from .backup import backup_events

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("STARTUP: server initializing", flush=True)

    # Start scheduler immediately so server is ready fast
    if os.environ.get("DISABLE_SCHEDULER", "false").lower() != "true":
        print("STARTUP: starting scheduler", flush=True)
        start_scheduler()
        print("STARTUP: syncing event reminders from Sheets", flush=True)
        try:
            sync_event_reminders()
        except Exception as e:
            logger.error("STARTUP: sync_event_reminders failed: %s", e)
        print("STARTUP: scheduler started", flush=True)
    else:
        print("STARTUP: scheduler disabled — worker handles reminders", flush=True)

    print("STARTUP: server ready — yielding to uvicorn", flush=True)
    yield

    # Sheets init runs in background after server is up
    print("STARTUP: verifying Google Sheets connection", flush=True)
    try:
        sheets.ensure_sheet_exists()
        logger.info("Google Sheets OK")
        print("STARTUP: Google Sheets OK", flush=True)
    except Exception as e:
        logger.error("Google Sheets init failed: %s", e)
        print(f"STARTUP: Google Sheets FAILED — {e}", flush=True)

    stop_scheduler()
    print("STARTUP: shutdown complete", flush=True)


app = FastAPI(title="LINE Event Reminder", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def get_webhook_handler(settings: Settings = Depends(get_settings)) -> WebhookHandler:
    return WebhookHandler(settings.line_channel_secret)


DEBUG_WEBHOOK = os.environ.get("DEBUG_WEBHOOK", "false").lower() == "true"


@app.post("/webhook")
async def webhook(request: Request, settings: Settings = Depends(get_settings)):
    body = await request.body()
    body_str = body.decode("utf-8")
    signature = request.headers.get("X-Line-Signature", "")

    if DEBUG_WEBHOOK:
        logger.warning("DEBUG_WEBHOOK=true — skipping signature validation")
        import json
        try:
            payload = json.loads(body_str)
            for evt in payload.get("events", []):
                logger.info("Webhook debug event=%s", evt)
        except Exception as e:
            logger.error("Webhook debug body parse failed: %s", e)
        return {"ok": True, "debug": True}

    handler = WebhookHandler(settings.line_channel_secret)

    @handler.add(MessageEvent)
    def on_message(event):
        from linebot.v3.webhooks import TextMessageContent
        text = event.message.text if isinstance(event.message, TextMessageContent) else None
        user_id = getattr(event.source, "user_id", "unknown")
        logger.info("Webhook message user=%s text=%r", user_id, text)
        handle_message(event)

    try:
        handler.handle(body_str, signature)
    except InvalidSignatureError:
        logger.error("Webhook invalid signature")
        raise HTTPException(status_code=400, detail="Invalid signature")
    except Exception as e:
        logger.error("WEBHOOK handler exception: %s", e, exc_info=True)
        raise
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


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
    event = sheets.create_event(req)
    schedule_event_reminder(event)
    return event


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
    await send_daily_summary()
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
