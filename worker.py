"""Render worker service — runs reminder + daily summary scheduler only."""
import asyncio
import logging
import os

import certifi
os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())

from app.scheduler import start_scheduler, stop_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("worker")


async def main() -> None:
    logger.info("Worker starting")
    start_scheduler()
    try:
        await asyncio.Event().wait()  # run until killed
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        stop_scheduler()
        logger.info("Worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
