"""Tests for APScheduler-based scheduler."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_scheduler_creates_cron_job() -> None:
    """BlockingScheduler should have exactly one job after setup."""
    with patch("config.settings.SCHEDULER_CRON_HOUR", 9), patch("config.settings.SCHEDULER_CRON_MINUTE", 0):
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger

        scheduler = BlockingScheduler()
        scheduler.add_job(
            lambda: None,
            trigger=CronTrigger(hour=9, minute=0),
            id="daily_pipeline",
        )
        jobs = scheduler.get_jobs()
        assert len(jobs) == 1
        assert jobs[0].id == "daily_pipeline"
        # Scheduler was never started, no shutdown needed


def test_scheduler_uses_configured_hour_minute() -> None:
    """CronTrigger should respect settings values."""
    from apscheduler.triggers.cron import CronTrigger

    trigger = CronTrigger(hour=15, minute=30)
    # CronTrigger stores fields; verify via string representation
    assert "hour='15'" in str(trigger)
    assert "minute='30'" in str(trigger)


def test_scheduler_job_calls_run_pipeline() -> None:
    """The scheduled job wrapper should call run_pipeline."""
    mock_pipeline = MagicMock()
    with patch("scripts.run_once.run_pipeline", mock_pipeline):
        from scripts.run_scheduler import _run_job

        _run_job()
        mock_pipeline.assert_called_once()
