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
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        return f"{d.day} {_THAI_MONTHS[d.month - 1]} {d.year + 543}"
    except ValueError:
        return date_str


def _thai_duration(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes} นาที"
    hours, mins = divmod(minutes, 60)
    return f"{hours} ชั่วโมง" if mins == 0 else f"{hours} ชั่วโมง {mins} นาที"


def _bullet_list(items: list[str]) -> str:
    return "\n".join(f"  • {item}" for item in items)


# ─── Source context ───────────────────────────────────────────────────────────

def _get_context(event: MessageEvent) -> dict:
    """
    Extract sender context from a webhook event.

    Returns a dict with:
      user_id      — LINE user ID of the sender
      group_id     — group/room ID (empty string for DMs)
      chat_type    — "user" | "group" | "room"
      target_id    — where to push replies (group if in group, else user)
      display_name — sender's display name
    """
    source = event.source
    user_id = source.user_id or ""
    api = _messaging_api()

    if source.type == "group":
        group_id = source.group_id
        chat_type = "group"
        target_id = group_id
        logger.info("Group event from user=%s group=%s", user_id, group_id)
        try:
            profile = api.get_group_member_profile(group_id, user_id)
            display_name = profile.display_name
        except Exception:
            display_name = user_id

    elif source.type == "room":
        group_id = source.room_id
        chat_type = "room"
        target_id = group_id
        logger.info("Room event from user=%s room=%s", user_id, group_id)
        try:
            profile = api.get_room_member_profile(group_id, user_id)
            display_name = profile.display_name
        except Exception:
            display_name = user_id

    else:  # "user" — direct message
        group_id = ""
        chat_type = "user"
        target_id = user_id
        logger.info("DM event from user=%s", user_id)
        try:
            profile = api.get_profile(user_id)
            display_name = profile.display_name
        except Exception:
            display_name = user_id

    return {
        "user_id": user_id,
        "group_id": group_id,
        "chat_type": chat_type,
        "target_id": target_id,
        "display_name": display_name,
    }


# ─── Message templates ────────────────────────────────────────────────────────

HELP_TEXT = """📅 ระบบแจ้งเตือนงาน

━━━━━━━━━━━━━━━━━━━
📌 คำสั่งที่ใช้ได้
━━━━━━━━━━━━━━━━━━━

/add — เพิ่มงานใหม่
/list — ดูรายการงานทั้งหมด
/today — ดูงานวันนี้
/delete <ID> — ยกเลิกงาน
/help — แสดงวิธีใช้งาน

━━━━━━━━━━━━━━━━━━━
📝 วิธีเพิ่มงาน (/add)
━━━━━━━━━━━━━━━━━━━

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
แจ้งเตือน: 60

💬 ใช้งานได้ทั้งในแชทส่วนตัวและกลุ่ม"""


def _fmt_event_created(event, added_by: str = "") -> str:
    lines = ["✅ เพิ่มงานสำเร็จ"]
    if added_by:
        lines.append(f"👤 เพิ่มโดย : {added_by}")
    lines += [
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
        lines += ["─────────────────────", "👤 ผู้รับผิดชอบ :", _bullet_list(responsible)]

    participants = event.participants_list()
    if participants:
        if not responsible:
            lines.append("─────────────────────")
        lines += ["👥 ผู้เข้าร่วม :", _bullet_list(participants)]

    if event.details:
        lines += ["─────────────────────", f"📝 รายละเอียด :\n{event.details}"]

    lines += ["─────────────────────", f"⏳ แจ้งเตือนก่อน {_thai_duration(event.reminder_minutes)}"]
    return "\n".join(lines)


def _fmt_event_list(events: list, title: str = "📋 รายการงาน") -> str:
    lines = [title, "─────────────────────"]
    for e in sorted(events, key=lambda x: (x.event_date, x.event_time)):
        lines.append(f"[{e.id}] {e.event_name}")
        lines.append(f"  🗓 {_thai_date(e.event_date)}  ⏰ {e.event_time} น.")
        if e.location:
            lines.append(f"  📍 {e.location}")
        if e.display_name and e.display_name != e.user_id:
            lines.append(f"  ✍️ {e.display_name}")
        lines.append(f"  ⏳ แจ้งเตือนก่อน {_thai_duration(e.reminder_minutes)}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _fmt_today(events: list, date_str: str) -> str:
    today_events = [e for e in events if e.event_date == date_str and e.status == EventStatus.active]
    thai_today = _thai_date(date_str)
    if not today_events:
        return f"📭 ไม่มีงานในวันนี้ ({thai_today})"
    return _fmt_event_list(today_events, title=f"📅 งานวันนี้ ({thai_today})")


def _fmt_daily_summary(events: list, date_str: str) -> str:
    thai_today = _thai_date(date_str)
    header = f"☀️ สรุปกิจกรรมวันนี้\nวันที่ {thai_today}"
    if not events:
        return f"{header}\n─────────────────────\n📭 ไม่มีกิจกรรมในวันนี้"

    sorted_events = sorted(events, key=lambda e: e.event_time)
    lines = [header, f"มีกิจกรรมทั้งหมด {len(sorted_events)} รายการ", "─────────────────────"]
    for i, e in enumerate(sorted_events, 1):
        lines.append(f"{i}) {e.event_name}")
        lines.append(f"   ⏰ {e.event_time} น.")
        if e.location:
            lines.append(f"   📍 {e.location}")
        responsible = e.responsible_list()
        if responsible:
            lines.append(f"   👤 {' | '.join(responsible)}")
        if i < len(sorted_events):
            lines.append("")
    return "\n".join(lines)


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
        lines += ["─────────────────────", "👤 ผู้รับผิดชอบ :", _bullet_list(responsible)]

    participants = event.participants_list()
    if participants:
        if not responsible:
            lines.append("─────────────────────")
        lines += ["👥 ผู้เข้าร่วม :", _bullet_list(participants)]

    if event.details:
        lines += ["─────────────────────", f"📝 รายละเอียด :\n{event.details}"]

    lines += ["─────────────────────", f"🔔 เริ่มในอีก {_thai_duration(event.reminder_minutes)}"]
    return "\n".join(lines)


# ─── Command parsers ──────────────────────────────────────────────────────────

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
    lines = text.strip().splitlines()
    if not lines[0].strip().lower().startswith("/add") or len(lines) < 2:
        return None

    parsed: dict = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if not val:
            continue
        internal = _FIELD_MAP.get(key)
        if internal:
            parsed[internal] = val

    if not (parsed.get("event_name") and parsed.get("event_date") and parsed.get("event_time")):
        return None

    for field in ("responsible", "participants"):
        if field in parsed:
            parsed[field] = "|".join(
                p.strip() for p in re.split(r"[|،,]", parsed[field]) if p.strip()
            )

    if "reminder" in parsed:
        try:
            parsed["reminder_minutes"] = int(parsed.pop("reminder"))
        except ValueError:
            parsed.pop("reminder", None)

    return parsed


def _parse_add_inline(text: str) -> dict | None:
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
    logger.info("REPLY token=%s text=%r", reply_token[:8] + "…", text[:80])
    try:
        api = _messaging_api()
        api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(type="text", text=text)],
            )
        )
        logger.info("REPLY sent OK")
    except Exception as e:
        logger.error("REPLY failed: %s", e, exc_info=True)
        raise


def push(target_id: str, text: str) -> None:
    import uuid as _uuid
    import traceback as _traceback
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo as _ZI
    call_id = _uuid.uuid4().hex[:8]
    caller = "".join(_traceback.format_stack()[-4:-1]).replace("\n", " | ")
    logger.warning(
        "PUSH_CALL call_id=%s target=%s text_preview=%r caller=%s",
        call_id, target_id, text[:60], caller,
    )
    try:
        api = _messaging_api()
        api.push_message(
            PushMessageRequest(
                to=target_id,
                messages=[TextMessage(type="text", text=text)],
            )
        )
        logger.warning("PUSH_OK call_id=%s at=%s", call_id,
            _dt.now(_ZI("Asia/Bangkok")).isoformat())
    except Exception as e:
        logger.error("PUSH_FAIL call_id=%s: %s", call_id, e, exc_info=True)
        raise


# ─── Main handler ─────────────────────────────────────────────────────────────

def handle_message(event: MessageEvent) -> None:
    try:
        _handle_message_inner(event)
    except Exception as e:
        logger.error("HANDLER unhandled exception: %s", e, exc_info=True)


def _handle_message_inner(event: MessageEvent) -> None:
    if not isinstance(event.message, TextMessageContent):
        logger.info("HANDLER non-text message type=%s — skipped", type(event.message).__name__)
        return

    text: str = event.message.text.strip()
    reply_token: str = event.reply_token
    logger.info("HANDLER raw text=%r reply_token=%s…", text, reply_token[:8])

    ctx = _get_context(event)
    user_id = ctx["user_id"]
    group_id = ctx["group_id"]
    chat_type = ctx["chat_type"]
    display_name = ctx["display_name"]
    in_group = bool(group_id)
    logger.info("HANDLER context user=%s display=%r chat=%s group=%s", user_id, display_name, chat_type, group_id or "-")

    settings = get_settings()
    cmd = text.split("\n")[0].strip().lower()
    logger.info("HANDLER cmd=%r", cmd)

    # ── /help ─────────────────────────────────────────────────────────────────
    if cmd == "/help":
        logger.info("HANDLER matched /help")
        reply(reply_token, HELP_TEXT)
        return

    # ── /add ──────────────────────────────────────────────────────────────────
    if cmd.startswith("/add"):
        logger.info("HANDLER matched /add — parsing")
        parsed = _parse_add_multiline(text) or _parse_add_inline(text)
        if not parsed:
            logger.warning("HANDLER /add parse failed — text=%r", text)
            reply(reply_token, "❌ รูปแบบไม่ถูกต้อง\n\nพิมพ์ /help เพื่อดูวิธีใช้งาน")
            return

        logger.info("HANDLER /add parsed=%s", parsed)
        try:
            event_dt = datetime.strptime(
                f"{parsed['event_date']} {parsed['event_time']}", "%Y-%m-%d %H:%M"
            )
        except ValueError:
            logger.warning("HANDLER /add invalid datetime date=%r time=%r", parsed.get("event_date"), parsed.get("event_time"))
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
            group_id=group_id,
            chat_type=chat_type,
        )
        logger.info("HANDLER /add writing to Sheets — req=%s", req.model_dump())
        try:
            created = sheets.create_event(req)
            logger.info("HANDLER /add Sheets write OK — event_id=%s", created.id)
        except Exception as e:
            logger.error("HANDLER /add Sheets write FAILED: %s", e, exc_info=True)
            reply(reply_token, f"❌ บันทึกงานไม่สำเร็จ: {e}")
            return
        try:
            from .scheduler import schedule_event_reminder
            schedule_event_reminder(created)
        except Exception as e:
            logger.error("HANDLER /add schedule_event_reminder failed: %s", e)
        reply(reply_token, _fmt_event_created(created, added_by=display_name if in_group else ""))
        return

    # ── /list ─────────────────────────────────────────────────────────────────
    if cmd == "/list":
        if in_group:
            events = [e for e in sheets.get_events_for_group(group_id) if e.status == EventStatus.active]
            title = "📋 รายการงานของกลุ่ม"
        else:
            events = [e for e in sheets.get_events_for_user(user_id) if e.status == EventStatus.active]
            title = "📋 รายการงานของคุณ"

        if not events:
            reply(reply_token, "📭 ยังไม่มีงานที่กำลังจะถึง\nพิมพ์ /add เพื่อเพิ่มงานใหม่")
            return
        reply(reply_token, _fmt_event_list(events, title=title))
        return

    # ── /today ────────────────────────────────────────────────────────────────
    if cmd == "/today":
        today_str = datetime.now().strftime("%Y-%m-%d")
        if in_group:
            events = sheets.get_events_for_group(group_id)
        else:
            events = sheets.get_events_for_user(user_id)
        reply(reply_token, _fmt_today(events, today_str))
        return

    # ── /delete ───────────────────────────────────────────────────────────────
    if cmd.startswith("/delete"):
        parts = text.strip().split()
        if len(parts) != 2:
            reply(reply_token, "❌ รูปแบบ: /delete <รหัส>\nตัวอย่าง: /delete A1B2C3D4")
            return

        event_id = parts[1].upper()
        all_events = sheets.get_all_events()

        if in_group:
            # Any group member can delete any event belonging to this group
            target = next((e for e in all_events if e.id == event_id and e.group_id == group_id), None)
        else:
            # In DM, users can only delete their own events
            target = next((e for e in all_events if e.id == event_id and e.user_id == user_id and not e.group_id), None)

        if not target:
            reply(reply_token, f"❌ ไม่พบรหัสงาน {event_id}")
            return

        sheets.delete_event(event_id)
        logger.info("Event %s deleted by %s in %s=%s", event_id, display_name, chat_type, group_id or user_id)
        reply(
            reply_token,
            f"🗑 ยกเลิกงานสำเร็จ\n"
            f"รหัส: {event_id}\n"
            f"เรื่อง: {target.event_name}",
        )
        return

    # Non-command messages — bot stays quiet in group chats
    logger.info("HANDLER no command matched for cmd=%r — no reply sent", cmd)
