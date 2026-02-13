"""Run the pipeline on a daily cron schedule using APScheduler."""

import signal
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from config import settings
from utils.logger import setup_logger

LOG_PATH = settings.PROJECT_ROOT / "logs" / "scheduler.log"


def _run_job() -> None:
    """Wrapper that imports and runs the pipeline, catching all errors."""
    log = setup_logger(LOG_PATH)
    log.info("===== Scheduled pipeline run =====")
    try:
        from scripts.run_once import run_pipeline

        run_pipeline()
    except Exception as exc:
        log.error("Pipeline run failed: %s", exc, exc_info=True)


def main() -> None:
    log = setup_logger(LOG_PATH)
    hour = settings.SCHEDULER_CRON_HOUR
    minute = settings.SCHEDULER_CRON_MINUTE
    log.info("Starting APScheduler — cron trigger at %02d:%02d daily", hour, minute)

    scheduler = BlockingScheduler()
    scheduler.add_job(
        _run_job,
        trigger=CronTrigger(hour=hour, minute=minute),
        id="daily_pipeline",
        name="Daily Pipeline Run",
        misfire_grace_time=3600,
    )

    def _shutdown(signum: int, _frame: object) -> None:
        log.info("Received signal %s, shutting down scheduler...", signum)
        scheduler.shutdown(wait=False)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped")


if __name__ == "__main__":
    main()
