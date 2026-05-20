import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from apscheduler.schedulers.asyncio import AsyncIOScheduler

BKK = ZoneInfo("Asia/Bangkok")

from .sheets import get_active_events, get_all_events, mark_reminder_sent
from .models import EventStatus
from .line_bot import push, _fmt_reminder, _fmt_daily_summary
from .backup import run_backup

logger = logging.getLogger(__name__)
# timezone="Asia/Bangkok" makes all cron triggers default to Bangkok time
scheduler = AsyncIOScheduler(timezone="Asia/Bangkok")

# Daily summary dedup: "YYYY-MM-DD:target_id" — cleared of stale dates each run
_sent_summaries: set[str] = set()


async def _check_and_send_reminders() -> None:
    """Stateless reminder dispatch — relies entirely on Sheets status as the lock."""
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
                "Event %s ('%s') → %s | reminder=%s event_time=%s",
                event.id, event.event_name, ctx,
                reminder_dt.strftime("%H:%M"), event.event_time,
            )
            # Lock in Sheets FIRST — at-most-once delivery guarantee
            mark_reminder_sent(event.id)
            push(event.target_id, _fmt_reminder(event))
            logger.info("Reminder delivered — event %s", event.id)
        except Exception as e:
            logger.error("Failed — event %s: %s", event.id, e)


async def _send_daily_summaries() -> None:
    global _sent_summaries
    now = datetime.now(BKK)
    today = now.strftime("%Y-%m-%d")
    week_end = (now + timedelta(days=7)).strftime("%Y-%m-%d")

    # Prune stale dedup keys from previous days
    _sent_summaries = {k for k in _sent_summaries if k.startswith(today)}

    try:
        all_events = get_all_events()
    except Exception as e:
        logger.error("Daily summary: failed to fetch events: %s", e)
        return

    # Targets considered "active": have at least one active event this week.
    # These are the only targets that receive a daily summary.
    active_targets: set[str] = {
        e.target_id for e in all_events
        if e.status == EventStatus.active and today <= e.event_date <= week_end
    }

    if not active_targets:
        logger.info("Daily summary: no active targets to notify")
        return

    # Collect today's events (active or already reminded) grouped by target
    today_by_target: dict[str, list] = {t: [] for t in active_targets}
    for e in all_events:
        if e.event_date == today and e.status in (EventStatus.active, EventStatus.sent):
            if e.target_id in today_by_target:
                today_by_target[e.target_id].append(e)

    for target_id, events in today_by_target.items():
        dedup_key = f"{today}:{target_id}"
        if dedup_key in _sent_summaries:
            logger.info("Daily summary: already sent to %s, skipping", target_id)
            continue

        try:
            push(target_id, _fmt_daily_summary(events, today))
            _sent_summaries.add(dedup_key)
            ctx = "no events" if not events else f"{len(events)} event(s)"
            logger.info("Daily summary sent to %s (%s)", target_id, ctx)
        except Exception as e:
            logger.error("Daily summary failed for %s: %s", target_id, e)


def start_scheduler() -> None:
    scheduler.add_job(
        _check_and_send_reminders,
        trigger="cron",
        minute="*",
        id="reminder_check",
        replace_existing=True,
        max_instances=1,   # prevent overlapping runs if Sheets API is slow
        coalesce=True,     # skip missed ticks instead of running them all at once
        misfire_grace_time=30,
    )
    scheduler.add_job(
        _send_daily_summaries,
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
    logger.info("Scheduler started: reminders every minute, summary at 06:00, backup at 00:00")


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
