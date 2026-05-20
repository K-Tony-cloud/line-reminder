import uuid
import logging
from datetime import datetime
from typing import Optional
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .models import Event, EventStatus, CreateEventRequest, UpdateEventRequest
from .config import get_settings

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SHEET_NAME = "Events"
HEADERS = [
    "ID", "UserID", "DisplayName", "EventName",
    "EventDate", "EventTime", "ReminderMinutes", "Status", "CreatedAt",
    "Location", "Responsible", "Participants", "Details",
]
# A=1 … M=13
COL_RANGE = "A:M"


def _get_service():
    settings = get_settings()
    creds = service_account.Credentials.from_service_account_file(
        settings.google_credentials_file, scopes=SCOPES
    )
    return build("sheets", "v4", credentials=creds)


def _spreadsheet_id() -> str:
    return get_settings().google_spreadsheet_id


def ensure_sheet_exists():
    """Create (or migrate) the Events sheet with the full header row."""
    service = _get_service()
    sheets = service.spreadsheets()
    sid = _spreadsheet_id()

    meta = sheets.get(spreadsheetId=sid).execute()
    existing_titles = [s["properties"]["title"] for s in meta["sheets"]]

    if SHEET_NAME not in existing_titles:
        sheets.batchUpdate(
            spreadsheetId=sid,
            body={"requests": [{"addSheet": {"properties": {"title": SHEET_NAME}}}]},
        ).execute()
        logger.info("Created sheet '%s'", SHEET_NAME)

    # Always ensure header row is up-to-date (handles migration)
    sheets.values().update(
        spreadsheetId=sid,
        range=f"{SHEET_NAME}!A1",
        valueInputOption="RAW",
        body={"values": [HEADERS]},
    ).execute()
    logger.info("Header row verified for '%s'", SHEET_NAME)


def _row_to_event(row: list) -> Event:
    # Pad to full header length so index access is safe
    while len(row) < len(HEADERS):
        row.append("")
    return Event(
        id=row[0],
        user_id=row[1],
        display_name=row[2],
        event_name=row[3],
        event_date=row[4],
        event_time=row[5],
        reminder_minutes=int(row[6]) if row[6] else 60,
        status=EventStatus(row[7]) if row[7] else EventStatus.active,
        created_at=row[8],
        location=row[9],
        responsible=row[10],
        participants=row[11],
        details=row[12],
    )


def get_all_events() -> list[Event]:
    service = _get_service()
    sid = _spreadsheet_id()
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=sid, range=f"{SHEET_NAME}!A2:{COL_RANGE.split(':')[1]}")
        .execute()
    )
    rows = result.get("values", [])
    logger.debug("Fetched %d data rows from Sheets", len(rows))
    events = []
    for row in rows:
        try:
            events.append(_row_to_event(row))
        except Exception as e:
            logger.warning("Skipping malformed row: %s — %s", row, e)
    return events


def get_events_for_user(user_id: str) -> list[Event]:
    return [e for e in get_all_events() if e.user_id == user_id]


def get_active_events() -> list[Event]:
    return [e for e in get_all_events() if e.status == EventStatus.active]


def create_event(req: CreateEventRequest) -> Event:
    event = Event(
        id=str(uuid.uuid4())[:8].upper(),
        user_id=req.user_id,
        display_name=req.display_name,
        event_name=req.event_name,
        event_date=req.event_date,
        event_time=req.event_time,
        reminder_minutes=req.reminder_minutes,
        status=EventStatus.active,
        created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        location=req.location,
        responsible=req.responsible,
        participants=req.participants,
        details=req.details,
    )
    row = [
        event.id, event.user_id, event.display_name, event.event_name,
        event.event_date, event.event_time, str(event.reminder_minutes),
        event.status.value, event.created_at,
        event.location, event.responsible, event.participants, event.details,
    ]
    service = _get_service()
    sid = _spreadsheet_id()
    logger.info("Appending event %s ('%s') to Sheets", event.id, event.event_name)
    try:
        result = service.spreadsheets().values().append(
            spreadsheetId=sid,
            range=f"{SHEET_NAME}!A1",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [row]},
        ).execute()
        logger.info("Append result: %s", result.get("updates", {}))
    except HttpError as e:
        logger.error("Sheets API error on append: %s", e)
        raise
    return event


def _find_row_index(event_id: str) -> Optional[int]:
    """1-based row number (row 2 = first data row after header)."""
    events = get_all_events()
    for i, e in enumerate(events):
        if e.id == event_id:
            return i + 2
    return None


def update_event(event_id: str, req: UpdateEventRequest) -> Optional[Event]:
    all_events = get_all_events()
    target = next((e for e in all_events if e.id == event_id), None)
    if not target:
        logger.warning("update_event: event %s not found", event_id)
        return None

    if req.event_name is not None:
        target.event_name = req.event_name
    if req.event_date is not None:
        target.event_date = req.event_date
    if req.event_time is not None:
        target.event_time = req.event_time
    if req.reminder_minutes is not None:
        target.reminder_minutes = req.reminder_minutes
    if req.status is not None:
        target.status = req.status
    if req.location is not None:
        target.location = req.location
    if req.responsible is not None:
        target.responsible = req.responsible
    if req.participants is not None:
        target.participants = req.participants
    if req.details is not None:
        target.details = req.details

    row_index = _find_row_index(event_id)
    if not row_index:
        return None

    row = [
        target.id, target.user_id, target.display_name, target.event_name,
        target.event_date, target.event_time, str(target.reminder_minutes),
        target.status.value, target.created_at,
        target.location, target.responsible, target.participants, target.details,
    ]
    end_col = COL_RANGE.split(":")[1]
    service = _get_service()
    sid = _spreadsheet_id()
    logger.info("Updating row %d for event %s", row_index, event_id)
    try:
        service.spreadsheets().values().update(
            spreadsheetId=sid,
            range=f"{SHEET_NAME}!A{row_index}:{end_col}{row_index}",
            valueInputOption="RAW",
            body={"values": [row]},
        ).execute()
    except HttpError as e:
        logger.error("Sheets API error on update: %s", e)
        raise
    return target


def delete_event(event_id: str) -> bool:
    return update_event(event_id, UpdateEventRequest(status=EventStatus.cancelled)) is not None


def mark_reminder_sent(event_id: str) -> None:
    update_event(event_id, UpdateEventRequest(status=EventStatus.sent))
