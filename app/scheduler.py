import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .sheets import get_active_events, get_all_events, get_event_by_id, mark_reminder_sent
from .models import EventStatus
from .line_bot import push, _fmt_reminder, _fmt_daily_summary
from .backup import run_backup

logger = logging.getLogger(__name__)
BKK = ZoneInfo("Asia/Bangkok")
scheduler = AsyncIOScheduler(timezone="Asia/Bangkok")

# Runtime dedup lock: dedupe_key → sent_at timestamp
# Prevents double-send even if _fire_reminder is somehow called twice
_recent_sends: dict[str, datetime] = {}
_DEDUP_TTL_SECONDS = 300  # 5 minutes


def _check_dedup(dedupe_key: str) -> bool:
    """Return True if this key was already sent recently (block). False = allow send."""
    now = datetime.now(BKK).replace(tzinfo=None)
    # Expire old keys
    expired = [k for k, t in _recent_sends.items() if (now - t).total_seconds() > _DEDUP_TTL_SECONDS]
    for k in expired:
        del _recent_sends[k]
    if dedupe_key in _recent_sends:
        logger.warning("DUPLICATE_BLOCKED key=%r sent_at=%s", dedupe_key, _recent_sends[dedupe_key])
        return True
    _recent_sends[dedupe_key] = now
    return False


# ─── Per-event reminder dispatch ──────────────────────────────────────────────

async def _fire_reminder(event_id: str) -> None:
    """APScheduler date-trigger callback for a single event reminder."""
    now = datetime.now(BKK)
    logger.info("Reminder firing — event_id=%s", event_id)

    try:
        event = get_event_by_id(event_id)
    except Exception as e:
        logger.error("Reminder %s — Sheets lookup failed: %s", event_id, e)
        return

    if event is None:
        logger.warning("Reminder %s — not found in Sheets", event_id)
        return
    if event.status != EventStatus.active:
        logger.info("Reminder %s — status=%s, skip", event_id, event.status.value)
        return

    target_id = event.target_id
    dedupe_key = f"{event_id}:{target_id}:{now.strftime('%Y-%m-%dT%H:%M')}"
    if _check_dedup(dedupe_key):
        return

    try:
        mark_reminder_sent(event_id)
        push(target_id, _fmt_reminder(event))
        logger.info("Reminder sent — event_id=%s target=%s", event_id, target_id)
    except Exception as e:
        _recent_sends.pop(dedupe_key, None)
        logger.error("Reminder failed — event_id=%s: %s", event_id, e)


def schedule_event_reminder(event) -> None:
    """Schedule (or replace) the date-trigger job for one event's reminder."""
    if not scheduler.running:
        logger.warning("schedule_event_reminder called before scheduler started — skipping %s", event.id)
        return

    job_id = f"reminder:{event.id}"
    reminder_dt = event.reminder_datetime()
    now = datetime.now(BKK).replace(tzinfo=None)

    if reminder_dt <= now:
        logger.info("SCHEDULE SKIP %s — reminder_dt=%s is in the past", event.id, reminder_dt)
        return

    existing = scheduler.get_job(job_id)
    if existing:
        logger.info("SCHEDULE REPLACE %s — old next_run=%s", job_id, existing.next_run_time)

    scheduler.add_job(
        _fire_reminder,
        trigger="date",
        run_date=reminder_dt,
        id=job_id,
        replace_existing=True,
        kwargs={"event_id": event.id},
        misfire_grace_time=120,
    )
    job = scheduler.get_job(job_id)
    logger.info(
        "SCHEDULED job=%s event=%s next_run=%s",
        job_id, event.id, job.next_run_time if job else "?",
    )


def unschedule_event_reminder(event_id: str) -> None:
    job_id = f"reminder:{event_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
        logger.info("UNSCHEDULED job=%s", job_id)


def sync_event_reminders() -> None:
    """On startup: remove stale reminder jobs, re-schedule all future active events."""
    # Remove all existing per-event reminder jobs
    removed = 0
    for job in scheduler.get_jobs():
        if job.id.startswith("reminder:"):
            scheduler.remove_job(job.id)
            removed += 1

    try:
        active = get_active_events()
    except Exception as e:
        logger.error("SYNC failed — could not fetch active events: %s", e)
        return

    now = datetime.now(BKK).replace(tzinfo=None)
    scheduled = 0
    skipped = 0
    for event in active:
        reminder_dt = event.reminder_datetime()
        if reminder_dt > now:
            schedule_event_reminder(event)
            scheduled += 1
        else:
            skipped += 1

    logger.info(
        "SYNC complete — removed=%d active=%d scheduled=%d past=%d",
        removed, len(active), scheduled, skipped,
    )
    _log_jobs()


# ─── Cron jobs ────────────────────────────────────────────────────────────────

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


# ─── Lifecycle ────────────────────────────────────────────────────────────────

def start_scheduler() -> None:
    pid = os.getpid()
    if scheduler.running:
        logger.warning("SCHEDULER ALREADY RUNNING — pid=%d skipping duplicate start", pid)
        _log_jobs()
        return

    logger.info("SCHEDULER INSTANCE STARTING — pid=%d", pid)

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
    logger.info("SCHEDULER INSTANCE STARTED — pid=%d", pid)
    _log_jobs()


def _log_jobs() -> None:
    jobs = scheduler.get_jobs()
    logger.info("SCHEDULER jobs (%d total):", len(jobs))
    for job in jobs:
        logger.info("  id=%s func=%s next_run=%s", job.id, job.func.__name__, job.next_run_time)


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("SCHEDULER stopped")
