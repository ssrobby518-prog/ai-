"""Run the pipeline in a loop with configurable interval (default 900s)."""

import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings
from utils.logger import setup_logger


def main() -> None:
    log = setup_logger(settings.LOG_PATH)
    interval = settings.SCHEDULER_INTERVAL_SECONDS
    log.info("Scheduler started. Interval: %d seconds", interval)

    # Import here so logger is initialized first
    from scripts.run_once import run_pipeline

    run_count = 0
    while True:
        run_count += 1
        log.info("===== Scheduled run #%d =====", run_count)
        try:
            run_pipeline()
        except KeyboardInterrupt:
            log.info("Scheduler stopped by user (Ctrl+C)")
            break
        except Exception as exc:
            log.error("Pipeline run #%d failed: %s", run_count, exc, exc_info=True)

        log.info("Sleeping %d seconds until next run...", interval)
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            log.info("Scheduler stopped by user (Ctrl+C)")
            break


if __name__ == "__main__":
    main()
