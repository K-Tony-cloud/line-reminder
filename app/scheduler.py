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

# In-process dedup: (event_id, reminder_minute) — second guard after Sheets status check
_sent_reminders: set[tuple[str, str]] = set()


async def check_reminders() -> None:
    """Poll active events and dispatch any due reminders.

    Two-layer at-most-once delivery:
      1. Sheets status=sent (survives restarts, guards against multiple instances)
      2. _sent_reminders in-process set (guards against same-process double-fire)
    Mark Sheets BEFORE pushing to guarantee delivery order.
    """
    now = datetime.now(BKK).replace(tzinfo=None, second=0, microsecond=0)
    now_key = now.strftime("%Y-%m-%d %H:%M")
    logger.info("Reminder check — Bangkok: %s", now_key)

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
        dedup_key = (event.id, now_key)
        if dedup_key in _sent_reminders:
            logger.warning("SKIP %s — already sent this minute (in-process dedup)", event.id)
            continue

        try:
            reminder_dt = event.reminder_datetime().replace(second=0, microsecond=0)
            ctx = f"group={event.group_id}" if event.group_id else f"user={event.user_id}"
            logger.info(
                "Dispatching %s ('%s') → %s | reminder=%s event=%s target=%s",
                event.id, event.event_name, ctx,
                reminder_dt.strftime("%Y-%m-%d %H:%M"), event.event_time, event.target_id,
            )
            _sent_reminders.add(dedup_key)
            mark_reminder_sent(event.id)
            push(event.target_id, _fmt_reminder(event))
            logger.info("Delivered — %s at %s → %s", event.id, now_key, event.target_id)
        except Exception as e:
            _sent_reminders.discard(dedup_key)
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
    if scheduler.running:
        logger.warning("SCHEDULER already running — skipping duplicate start")
        _log_jobs()
        return

    scheduler.add_job(
        check_reminders,
        trigger="cron",
        minute="*",
        id="check_reminders",
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
        id="send_daily_summary",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        run_backup,
        trigger="cron",
        hour=0,
        minute=0,
        id="run_backup",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    logger.info("SCHEDULER started")
    _log_jobs()


def _log_jobs() -> None:
    jobs = scheduler.get_jobs()
    logger.info("SCHEDULER registered jobs (%d):", len(jobs))
    for job in jobs:
        logger.info("  job id=%s func=%s next_run=%s", job.id, job.func.__name__, job.next_run_time)


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("SCHEDULER stopped")
