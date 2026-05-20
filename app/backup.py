import logging
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .utils import now_bkk
from .sheets import SHEET_NAME, COL_RANGE, _spreadsheet_id, get_google_credentials
from .config import get_settings

logger = logging.getLogger(__name__)

RETENTION_DAYS = 14  # keep this many most-recent backup sheets


def _get_service():
    return build("sheets", "v4", credentials=get_google_credentials())


def _backup_spreadsheet_id() -> str:
    sid = get_settings().google_backup_spreadsheet_id
    if not sid:
        raise ValueError("GOOGLE_BACKUP_SPREADSHEET_ID is not set")
    return sid


def _apply_retention(sheets_api, backup_sid: str, keep: int = RETENTION_DAYS) -> None:
    """Delete backup sheets older than the most recent `keep` snapshots."""
    meta = sheets_api.get(spreadsheetId=backup_sid).execute()
    backup_sheets = sorted(
        [s for s in meta["sheets"] if s["properties"]["title"].startswith("Backup_")],
        key=lambda s: s["properties"]["title"],
        reverse=True,
    )
    to_delete = backup_sheets[keep:]
    if not to_delete:
        return
    requests = [
        {"deleteSheet": {"sheetId": s["properties"]["sheetId"]}}
        for s in to_delete
    ]
    sheets_api.batchUpdate(
        spreadsheetId=backup_sid,
        body={"requests": requests},
    ).execute()
    deleted_names = [s["properties"]["title"] for s in to_delete]
    logger.info("BACKUP retention: deleted %d old sheets: %s", len(deleted_names), deleted_names)


def backup_events() -> dict:
    """Copy all rows from the main Events sheet into a dated sheet in the backup spreadsheet."""
    started_at = now_bkk().isoformat()
    sheet_name = "Backup_" + now_bkk().strftime("%Y%m%d")
    logger.info("BACKUP_STARTED sheet=%s at=%s", sheet_name, started_at)

    try:
        service = _get_service()
        sheets_api = service.spreadsheets()
        backup_sid = _backup_spreadsheet_id()

        # Read all data (including header) from source sheet
        end_col = COL_RANGE.split(":")[1]
        source_range = f"{SHEET_NAME}!A1:{end_col}"
        result = sheets_api.values().get(
            spreadsheetId=_spreadsheet_id(), range=source_range
        ).execute()
        rows = result.get("values", [])
        logger.info("BACKUP: read %d rows from source", len(rows))

        # Get existing sheets in backup spreadsheet
        meta = sheets_api.get(spreadsheetId=backup_sid).execute()
        existing = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}

        if sheet_name in existing:
            sheets_api.values().clear(
                spreadsheetId=backup_sid,
                range=f"{sheet_name}!A1:{end_col}",
            ).execute()
        else:
            sheets_api.batchUpdate(
                spreadsheetId=backup_sid,
                body={"requests": [{"addSheet": {"properties": {"title": sheet_name}}}]},
            ).execute()

        data_rows = 0
        if rows:
            sheets_api.values().update(
                spreadsheetId=backup_sid,
                range=f"{sheet_name}!A1",
                valueInputOption="RAW",
                body={"values": rows},
            ).execute()
            data_rows = len(rows) - 1  # exclude header

        # Enforce retention policy
        _apply_retention(sheets_api, backup_sid, keep=RETENTION_DAYS)

        logger.info(
            "BACKUP_COMPLETED sheet=%s rows=%d completed_at=%s",
            sheet_name, data_rows, now_bkk().isoformat(),
        )
        return {"status": "ok", "sheet": sheet_name, "rows": data_rows}

    except Exception as e:
        logger.error("BACKUP_FAILED sheet=%s error=%s", sheet_name, e, exc_info=True)
        raise


async def run_backup() -> None:
    try:
        backup_events()
    except Exception as e:
        logger.error("Scheduled backup failed: %s", e)
