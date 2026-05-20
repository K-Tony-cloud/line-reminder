from datetime import datetime
from zoneinfo import ZoneInfo

BKK = ZoneInfo("Asia/Bangkok")


def now_bkk() -> datetime:
    return datetime.now(BKK)
