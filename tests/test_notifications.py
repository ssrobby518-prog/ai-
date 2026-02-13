"""Tests for core.notifications module."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.notifications import (
    notify_email,
    notify_notion_run,
    notify_slack,
    send_all_notifications,
)

TS = "2025-01-01T00:00:00"
REPORT = "/tmp/digest.md"


# --- Slack ---


def test_slack_skip_when_unconfigured() -> None:
    with patch("core.notifications.settings") as mock_settings:
        mock_settings.SLACK_WEBHOOK_URL = ""
        assert notify_slack(TS, 10, True, REPORT) is False


def test_slack_send_when_configured() -> None:
    with patch("core.notifications.settings") as mock_settings, \
         patch("core.notifications.requests.post") as mock_post:
        mock_settings.SLACK_WEBHOOK_URL = "https://hooks.slack.com/test"
        mock_post.return_value = MagicMock(status_code=200)
        mock_post.return_value.raise_for_status = MagicMock()
        assert notify_slack(TS, 5, True, REPORT) is True
        mock_post.assert_called_once()


def test_slack_handles_failure() -> None:
    with patch("core.notifications.settings") as mock_settings, \
         patch("core.notifications.requests.post", side_effect=Exception("boom")):
        mock_settings.SLACK_WEBHOOK_URL = "https://hooks.slack.com/test"
        assert notify_slack(TS, 5, True, REPORT) is False


# --- Email ---


def test_email_skip_when_unconfigured() -> None:
    with patch("core.notifications.settings") as mock_settings:
        mock_settings.SMTP_HOST = ""
        mock_settings.ALERT_EMAIL = ""
        assert notify_email(TS, 10, True, REPORT) is False


def test_email_send_when_configured() -> None:
    with patch("core.notifications.settings") as mock_settings, \
         patch("core.notifications.smtplib.SMTP") as mock_smtp_cls:
        mock_settings.SMTP_HOST = "smtp.test.com"
        mock_settings.SMTP_PORT = 587
        mock_settings.SMTP_USER = "user"
        mock_settings.SMTP_PASS = "pass"
        mock_settings.ALERT_EMAIL = "admin@test.com"

        mock_smtp = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        assert notify_email(TS, 10, True, REPORT) is True


# --- Notion ---


def test_notion_skip_when_unconfigured() -> None:
    with patch("core.notifications.settings") as mock_settings:
        mock_settings.NOTION_TOKEN = ""
        mock_settings.NOTION_DATABASE_ID = ""
        assert notify_notion_run(TS, 10, True, REPORT) is False


def test_notion_send_when_configured() -> None:
    with patch("core.notifications.settings") as mock_settings, \
         patch("core.notifications.requests.post") as mock_post:
        mock_settings.NOTION_TOKEN = "secret_token"
        mock_settings.NOTION_DATABASE_ID = "db123"
        mock_post.return_value = MagicMock(status_code=200)
        mock_post.return_value.raise_for_status = MagicMock()
        assert notify_notion_run(TS, 5, True, REPORT) is True


# --- send_all_notifications ---


def test_send_all_returns_dict() -> None:
    with patch("core.notifications.settings") as mock_settings:
        mock_settings.SLACK_WEBHOOK_URL = ""
        mock_settings.SMTP_HOST = ""
        mock_settings.ALERT_EMAIL = ""
        mock_settings.NOTION_TOKEN = ""
        mock_settings.NOTION_DATABASE_ID = ""
        result = send_all_notifications(TS, 0, True, "")
        assert isinstance(result, dict)
        assert set(result.keys()) == {"slack", "email", "notion"}


def test_send_all_never_crashes() -> None:
    """Even if individual channels raise, send_all should not crash."""
    with patch("core.notifications.notify_slack", side_effect=Exception("slack boom")), \
         patch("core.notifications.notify_email", side_effect=Exception("email boom")), \
         patch("core.notifications.notify_notion_run", side_effect=Exception("notion boom")):
        result = send_all_notifications(TS, 0, True, "")
        assert isinstance(result, dict)
        assert all(v is False for v in result.values())
