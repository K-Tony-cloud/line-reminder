import re
import logging
from datetime import datetime
from linebot.v3.messaging import (
    ApiClient, MessagingApi, Configuration,
    ReplyMessageRequest, PushMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

from .config import get_settings
from .models import CreateEventRequest, EventStatus
from . import sheets

logger = logging.getLogger(__name__)

# ─── Thai locale helpers ──────────────────────────────────────────────────────

_THAI_MONTHS = [
    "ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
    "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค.",
]


def _thai_date(date_str: str) -> str:
    """Convert YYYY-MM-DD → '11 พ.ค. 2569' (Buddhist Era)."""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        return f"{d.day} {_THAI_MONTHS[d.month - 1]} {d.year + 543}"
    except ValueError:
        return date_str


def _thai_duration(minutes: int) -> str:
    """Convert minutes → human-readable Thai string."""
    if minutes < 60:
        return f"{minutes} นาที"
    hours, mins = divmod(minutes, 60)
    if mins == 0:
        return f"{hours} ชั่วโมง"
    return f"{hours} ชั่วโมง {mins} นาที"


def _bullet_list(items: list[str]) -> str:
    return "\n".join(f"  • {item}" for item in items)


# ─── Message templates ────────────────────────────────────────────────────────

HELP_TEXT = """📅 ระบบแจ้งเตือนงาน

━━━━━━━━━━━━━━━━━━━
📌 คำสั่งที่ใช้ได้
━━━━━━━━━━━━━━━━━━━

/add — เพิ่มงานใหม่ (รองรับหลายบรรทัด)
/list — ดูรายการงานของฉัน
/delete <ID> — ยกเลิกงาน
/help — แสดงวิธีใช้งาน

━━━━━━━━━━━━━━━━━━━
📝 วิธีเพิ่มงาน (/add)
━━━━━━━━━━━━━━━━━━━

พิมพ์ /add ตามด้วยรายละเอียด:

/add
เรื่อง: ชื่องาน
วันที่: YYYY-MM-DD
เวลา: HH:MM
สถานที่: ห้องประชุม (ไม่บังคับ)
ผู้รับผิดชอบ: ชื่อ1|ชื่อ2 (ไม่บังคับ)
ผู้เข้าร่วม: ชื่อ1|ชื่อ2 (ไม่บังคับ)
รายละเอียด: ข้อความ (ไม่บังคับ)
แจ้งเตือน: 60 (นาที, ไม่บังคับ)

━━━━━━━━━━━━━━━━━━━
💡 ตัวอย่าง
━━━━━━━━━━━━━━━━━━━

/add
เรื่อง: ประชุมจราจร
วันที่: 2026-05-15
เวลา: 14:00
สถานที่: ห้องประชุมชั้น 2
ผู้รับผิดชอบ: สว.จร.|รอง สว.
ผู้เข้าร่วม: ชุดสายตรวจ|เจ้าหน้าที่เวร
รายละเอียด: เตรียมเอกสารรายงาน
แจ้งเตือน: 60"""


def _fmt_event_created(event) -> str:
    lines = [
        "✅ เพิ่มงานสำเร็จ",
        f"🔑 รหัส : {event.id}",
        "─────────────────────",
        f"📌 เรื่อง  : {event.event_name}",
        f"🗓 วันที่  : {_thai_date(event.event_date)}",
        f"⏰ เวลา   : {event.event_time} น.",
    ]
    if event.location:
        lines.append(f"📍 สถานที่ : {event.location}")

    responsible = event.responsible_list()
    if responsible:
        lines.append("─────────────────────")
        lines.append("👤 ผู้รับผิดชอบ :")
        lines.append(_bullet_list(responsible))

    participants = event.participants_list()
    if participants:
        if not responsible:
            lines.append("─────────────────────")
        lines.append("👥 ผู้เข้าร่วม :")
        lines.append(_bullet_list(participants))

    if event.details:
        lines.append("─────────────────────")
        lines.append(f"📝 รายละเอียด :\n{event.details}")

    lines.append("─────────────────────")
    lines.append(f"⏳ แจ้งเตือนก่อน {_thai_duration(event.reminder_minutes)}")
    return "\n".join(lines)


def _fmt_event_list(events) -> str:
    lines = ["📋 รายการงานของคุณ", "─────────────────────"]
    for e in sorted(events, key=lambda x: (x.event_date, x.event_time)):
        lines.append(
            f"[{e.id}] {e.event_name}\n"
            f"  🗓 {_thai_date(e.event_date)}  ⏰ {e.event_time} น."
        )
        if e.location:
            lines.append(f"  📍 {e.location}")
        lines.append(f"  ⏳ แจ้งเตือนก่อน {_thai_duration(e.reminder_minutes)}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _fmt_reminder(event) -> str:
    lines = [
        "⏰ แจ้งเตือนงาน",
        "─────────────────────",
        f"📌 เรื่อง  : {event.event_name}",
        f"🗓 วันที่  : {_thai_date(event.event_date)}",
        f"⏰ เวลา   : {event.event_time} น.",
    ]
    if event.location:
        lines.append(f"📍 สถานที่ : {event.location}")

    responsible = event.responsible_list()
    if responsible:
        lines.append("─────────────────────")
        lines.append("👤 ผู้รับผิดชอบ :")
        lines.append(_bullet_list(responsible))

    participants = event.participants_list()
    if participants:
        if not responsible:
            lines.append("─────────────────────")
        lines.append("👥 ผู้เข้าร่วม :")
        lines.append(_bullet_list(participants))

    if event.details:
        lines.append("─────────────────────")
        lines.append(f"📝 รายละเอียด :\n{event.details}")

    lines.append("─────────────────────")
    lines.append(f"🔔 เริ่มในอีก {_thai_duration(event.reminder_minutes)}")
    return "\n".join(lines)


# ─── Command parsers ──────────────────────────────────────────────────────────

# Map Thai field keys → internal names
_FIELD_MAP = {
    "เรื่อง": "event_name",   "name": "event_name",
    "วันที่": "event_date",   "date": "event_date",
    "เวลา": "event_time",     "time": "event_time",
    "สถานที่": "location",    "location": "location",
    "ผู้รับผิดชอบ": "responsible", "responsible": "responsible",
    "ผู้เข้าร่วม": "participants",  "participants": "participants",
    "รายละเอียด": "details",  "details": "details",
    "แจ้งเตือน": "reminder",  "remind": "reminder",
}


def _parse_add_multiline(text: str) -> dict | None:
    """
    Parse multi-line /add block:
        /add
        เรื่อง: ชื่องาน
        วันที่: 2026-05-15
        เวลา: 14:00
        ...
    """
    lines = text.strip().splitlines()
    if not lines[0].strip().lower().startswith("/add"):
        return None
    if len(lines) < 2:
        return None  # only "/add" with no fields — fall through to inline parser

    parsed: dict = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if not val:
            continue
        internal = _FIELD_MAP.get(key)
        if internal:
            parsed[internal] = val

    if not parsed.get("event_name") or not parsed.get("event_date") or not parsed.get("event_time"):
        return None

    # Normalise pipe-separated lists (also accept comma-separated and convert)
    for field in ("responsible", "participants"):
        if field in parsed:
            # Accept either | or , as separator; normalise to |
            parsed[field] = "|".join(
                p.strip() for p in re.split(r"[|،,]", parsed[field]) if p.strip()
            )

    # Reminder minutes
    if "reminder" in parsed:
        try:
            parsed["reminder_minutes"] = int(parsed.pop("reminder"))
        except ValueError:
            parsed.pop("reminder", None)

    return parsed


def _parse_add_inline(text: str) -> dict | None:
    """
    Fallback single-line parser:
        /add <name> <YYYY-MM-DD> <HH:MM> [remind=N]
    """
    pattern = r"^/add\s+(.+?)\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})(?:\s+remind=(\d+))?$"
    m = re.match(pattern, text.strip(), re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    return {
        "event_name": m.group(1).strip(),
        "event_date": m.group(2),
        "event_time": m.group(3),
        "reminder_minutes": int(m.group(4)) if m.group(4) else None,
    }


# ─── LINE API helpers ─────────────────────────────────────────────────────────

def _messaging_api() -> MessagingApi:
    settings = get_settings()
    config = Configuration(access_token=settings.line_channel_access_token)
    return MessagingApi(ApiClient(config))


def reply(reply_token: str, text: str) -> None:
    api = _messaging_api()
    api.reply_message(
        ReplyMessageRequest(
            reply_token=reply_token,
            messages=[TextMessage(type="text", text=text)],
        )
    )


def push(user_id: str, text: str) -> None:
    api = _messaging_api()
    api.push_message(
        PushMessageRequest(
            to=user_id,
            messages=[TextMessage(type="text", text=text)],
        )
    )


# ─── Main handler ─────────────────────────────────────────────────────────────

def handle_message(event: MessageEvent) -> None:
    if not isinstance(event.message, TextMessageContent):
        return

    text: str = event.message.text.strip()
    user_id: str = event.source.user_id
    reply_token: str = event.reply_token

    try:
        api = _messaging_api()
        profile = api.get_profile(user_id)
        display_name = profile.display_name
    except Exception:
        display_name = user_id

    settings = get_settings()
    cmd = text.split("\n")[0].strip().lower()

    # /help
    if cmd == "/help":
        reply(reply_token, HELP_TEXT)
        return

    # /add (multi-line or inline)
    if cmd.startswith("/add"):
        parsed = _parse_add_multiline(text) or _parse_add_inline(text)

        if not parsed:
            reply(
                reply_token,
                "❌ รูปแบบไม่ถูกต้อง\n\nพิมพ์ /help เพื่อดูวิธีใช้งาน",
            )
            return

        try:
            event_dt = datetime.strptime(
                f"{parsed['event_date']} {parsed['event_time']}", "%Y-%m-%d %H:%M"
            )
        except ValueError:
            reply(reply_token, "❌ วันที่หรือเวลาไม่ถูกต้อง\nรูปแบบ: YYYY-MM-DD และ HH:MM")
            return

        if event_dt <= datetime.now():
            reply(reply_token, "❌ กรุณาระบุวันที่และเวลาในอนาคต")
            return

        reminder_minutes = parsed.get("reminder_minutes") or settings.default_reminder_minutes
        req = CreateEventRequest(
            user_id=user_id,
            display_name=display_name,
            event_name=parsed["event_name"],
            event_date=parsed["event_date"],
            event_time=parsed["event_time"],
            reminder_minutes=reminder_minutes,
            location=parsed.get("location", ""),
            responsible=parsed.get("responsible", ""),
            participants=parsed.get("participants", ""),
            details=parsed.get("details", ""),
        )
        try:
            created = sheets.create_event(req)
            reply(reply_token, _fmt_event_created(created))
        except Exception as e:
            logger.error("create_event failed: %s", e, exc_info=True)
            reply(reply_token, f"❌ บันทึกงานไม่สำเร็จ: {e}")
        return

    # /list
    if cmd == "/list":
        user_events = [
            e for e in sheets.get_events_for_user(user_id)
            if e.status == EventStatus.active
        ]
        if not user_events:
            reply(reply_token, "📭 ยังไม่มีงานที่กำลังจะถึง\nพิมพ์ /add เพื่อเพิ่มงานใหม่")
            return
        reply(reply_token, _fmt_event_list(user_events))
        return

    # /delete <ID>
    if cmd.startswith("/delete"):
        parts = text.strip().split()
        if len(parts) != 2:
            reply(reply_token, "❌ รูปแบบ: /delete <รหัส>\nตัวอย่าง: /delete A1B2C3D4")
            return

        event_id = parts[1].upper()
        all_events = sheets.get_all_events()
        target = next(
            (e for e in all_events if e.id == event_id and e.user_id == user_id), None
        )
        if not target:
            reply(reply_token, f"❌ ไม่พบรหัสงาน {event_id}")
            return

        sheets.delete_event(event_id)
        reply(
            reply_token,
            f"🗑 ยกเลิกงานสำเร็จ\n"
            f"รหัส: {event_id}\n"
            f"เรื่อง: {target.event_name}",
        )
        return

    # Unknown command
    reply(
        reply_token,
        "👋 สวัสดี! ฉันคือระบบแจ้งเตือนงาน\nพิมพ์ /help เพื่อดูคำสั่งที่ใช้ได้",
    )
