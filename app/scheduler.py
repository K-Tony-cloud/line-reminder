import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .sheets import get_active_events, get_all_events, mark_reminder_sent
from .models import EventStatus
from .line_bot import push, _fmt_reminder, _fmt_daily_summary
from .backup import run_backup

logger = logging.getLogger(__name__)
BKK = ZoneInfo("Asia/Bangkok")
scheduler = AsyncIOScheduler(timezone="Asia/Bangkok")


async def check_reminders() -> None:
    """Poll active events and dispatch any due reminders.

    Stateless — Google Sheets status=active is the only lock.
    Mark as sent BEFORE pushing to guarantee at-most-once delivery.
    """
    now = datetime.now(BKK).replace(tzinfo=None, second=0, microsecond=0)
    logger.info("Reminder check — Bangkok: %s", now.strftime("%Y-%m-%d %H:%M"))

    try:
        active = get_active_events()
    except Exception as e:
        logger.error("Failed to fetch active events: %s", e)
        return

    due = [
        e for e in active
        if e.reminder_datetime().replace(second=0, microsecond=0) <= now
        < e.reminder_datetime().replace(second=0, microsecond=0) + timedelta(minutes=1)
    ]

    logger.info("Active: %d | Due: %d", len(active), len(due))

    for event in due:
        try:
            reminder_dt = event.reminder_datetime().replace(second=0, microsecond=0)
            ctx = f"group={event.group_id}" if event.group_id else f"user={event.user_id}"
            logger.info(
                "Dispatching %s ('%s') → %s | reminder=%s event=%s",
                event.id, event.event_name, ctx,
                reminder_dt.strftime("%H:%M"), event.event_time,
            )
            mark_reminder_sent(event.id)
            push(event.target_id, _fmt_reminder(event))
            logger.info("Delivered — %s", event.id)
        except Exception as e:
            logger.error("Failed — %s: %s", event.id, e)


async def send_daily_summary() -> None:
    """Send morning event summary to all targets with active events this week."""
    now = datetime.now(BKK)
    today = now.strftime("%Y-%m-%d")
    week_end = (now + timedelta(days=7)).strftime("%Y-%m-%d")
    logger.info("Daily summary — %s", today)

    try:
        all_events = get_all_events()
    except Exception as e:
        logger.error("Daily summary: fetch failed: %s", e)
        return

    active_targets: set[str] = {
        e.target_id for e in all_events
        if e.status == EventStatus.active and today <= e.event_date <= week_end
    }

    if not active_targets:
        logger.info("Daily summary: no active targets")
        return

    today_by_target: dict[str, list] = {t: [] for t in active_targets}
    for e in all_events:
        if e.event_date == today and e.status in (EventStatus.active, EventStatus.sent):
            if e.target_id in today_by_target:
                today_by_target[e.target_id].append(e)

    for target_id, events in today_by_target.items():
        try:
            push(target_id, _fmt_daily_summary(events, today))
            logger.info("Daily summary → %s (%d events)", target_id, len(events))
        except Exception as e:
            logger.error("Daily summary failed → %s: %s", target_id, e)


def start_scheduler() -> None:
    scheduler.add_job(
        check_reminders,
        trigger="cron",
        minute="*",
        id="reminders",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=30,
    )
    scheduler.add_job(
        send_daily_summary,
        trigger="cron",
        hour=6,
        minute=0,
        id="daily_summary",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        run_backup,
        trigger="cron",
        hour=0,
        minute=0,
        id="daily_backup",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    logger.info("Scheduler started — reminders every minute, summary 06:00, backup 00:00")


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
