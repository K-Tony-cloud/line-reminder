import logging
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .sheets import get_active_events, mark_reminder_sent
from .line_bot import push, _fmt_reminder

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()


async def _check_and_send_reminders() -> None:
    now = datetime.now().replace(second=0, microsecond=0)
    try:
        events = get_active_events()
    except Exception as e:
        logger.error("Failed to fetch events: %s", e)
        return

    for event in events:
        try:
            reminder_dt = event.reminder_datetime().replace(second=0, microsecond=0)
            if reminder_dt != now:
                continue

            # Push to group/room if the event originated there; otherwise to the user
            target = event.target_id
            ctx = f"group={event.group_id}" if event.group_id else f"user={event.user_id}"
            push(target, _fmt_reminder(event))
            mark_reminder_sent(event.id)
            logger.info("Sent reminder for event %s ('%s') → %s", event.id, event.event_name, ctx)

        except Exception as e:
            logger.error("Error processing event %s: %s", event.id, e)


def start_scheduler() -> None:
    scheduler.add_job(
        _check_and_send_reminders,
        trigger="cron",
        minute="*",
        id="reminder_check",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Reminder scheduler started (checks every minute)")


def stop_scheduler() -> None:
    scheduler.shutdown(wait=False)
