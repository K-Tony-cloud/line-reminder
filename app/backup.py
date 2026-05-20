import logging
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .utils import now_bkk
from .sheets import SHEET_NAME, COL_RANGE, _spreadsheet_id, get_google_credentials
from .config import get_settings

logger = logging.getLogger(__name__)


def _get_service():
    return build("sheets", "v4", credentials=get_google_credentials())


def _backup_spreadsheet_id() -> str:
    sid = get_settings().google_backup_spreadsheet_id
    if not sid:
        raise ValueError("GOOGLE_BACKUP_SPREADSHEET_ID is not set")
    return sid


def backup_events() -> dict:
    """Copy all rows from the main Events sheet into a dated sheet in the backup spreadsheet."""
    service = _get_service()
    sheets_api = service.spreadsheets()

    # Read all data (including header) from source sheet
    end_col = COL_RANGE.split(":")[1]
    source_range = f"{SHEET_NAME}!A1:{end_col}"
    result = (
        sheets_api.values()
        .get(spreadsheetId=_spreadsheet_id(), range=source_range)
        .execute()
    )
    rows = result.get("values", [])
    logger.info("Backup: read %d rows (including header) from source sheet", len(rows))

    sheet_name = "Backup_" + now_bkk().strftime("%Y_%m_%d")
    backup_sid = _backup_spreadsheet_id()

    # Get existing sheets in backup spreadsheet
    meta = sheets_api.get(spreadsheetId=backup_sid).execute()
    existing = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}

    if sheet_name in existing:
        # Clear existing sheet so backup is idempotent
        sheets_api.values().clear(
            spreadsheetId=backup_sid,
            range=f"{sheet_name}!A1:{end_col}",
        ).execute()
        logger.info("Backup: cleared existing sheet '%s'", sheet_name)
    else:
        # Create new sheet
        sheets_api.batchUpdate(
            spreadsheetId=backup_sid,
            body={"requests": [{"addSheet": {"properties": {"title": sheet_name}}}]},
        ).execute()
        logger.info("Backup: created sheet '%s'", sheet_name)

    if not rows:
        logger.info("Backup: no data to write")
        return {"status": "ok", "sheet": sheet_name, "rows": 0}

    sheets_api.values().update(
        spreadsheetId=backup_sid,
        range=f"{sheet_name}!A1",
        valueInputOption="RAW",
        body={"values": rows},
    ).execute()

    data_rows = len(rows) - 1  # exclude header
    logger.info("Backup: wrote %d data rows to '%s'", data_rows, sheet_name)
    return {"status": "ok", "sheet": sheet_name, "rows": data_rows}


async def run_backup() -> None:
    try:
        result = backup_events()
        logger.info("Scheduled backup completed: %s", result)
    except Exception as e:
        logger.error("Scheduled backup failed: %s", e)
