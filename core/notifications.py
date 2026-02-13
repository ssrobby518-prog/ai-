"""Pipeline run notifications — Slack, Email, Notion.

Every function is safe: unconfigured channels return False, exceptions are
logged but never propagated.
"""

from __future__ import annotations

import smtplib
from email.mime.text import MIMEText

import requests
from config import settings
from utils.logger import get_logger


def _build_message(timestamp: str, item_count: int, success: bool, report_path: str) -> str:
    status = "SUCCESS" if success else "FAILURE"
    return (
        f"Pipeline Run Report\n"
        f"  Status:     {status}\n"
        f"  Timestamp:  {timestamp}\n"
        f"  Items:      {item_count}\n"
        f"  Report:     {report_path}\n"
    )


def notify_slack(timestamp: str, item_count: int, success: bool, report_path: str) -> bool:
    """Post a summary to Slack via webhook. Returns True on success."""
    log = get_logger()
    webhook = settings.SLACK_WEBHOOK_URL
    if not webhook:
        log.debug("Slack not configured, skipping")
        return False
    try:
        text = _build_message(timestamp, item_count, success, report_path)
        resp = requests.post(webhook, json={"text": text}, timeout=10)
        resp.raise_for_status()
        log.info("Slack notification sent")
        return True
    except Exception as exc:
        log.error("Slack notification failed: %s", exc)
        return False


def notify_email(timestamp: str, item_count: int, success: bool, report_path: str) -> bool:
    """Send a summary email via SMTP. Returns True on success."""
    log = get_logger()
    if not settings.SMTP_HOST or not settings.ALERT_EMAIL:
        log.debug("Email not configured, skipping")
        return False
    try:
        body = _build_message(timestamp, item_count, success, report_path)
        status = "SUCCESS" if success else "FAILURE"
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = f"AI Intel Pipeline {status} — {item_count} items"
        msg["From"] = settings.SMTP_USER or "noreply@ai-intel.local"
        msg["To"] = settings.ALERT_EMAIL

        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as smtp:
            smtp.ehlo()
            smtp.starttls()
            if settings.SMTP_USER and settings.SMTP_PASS:
                smtp.login(settings.SMTP_USER, settings.SMTP_PASS)
            smtp.sendmail(msg["From"], [settings.ALERT_EMAIL], msg.as_string())
        log.info("Email notification sent to %s", settings.ALERT_EMAIL)
        return True
    except Exception as exc:
        log.error("Email notification failed: %s", exc)
        return False


def notify_notion_run(timestamp: str, item_count: int, success: bool, report_path: str) -> bool:
    """Log the pipeline run to Notion database. Returns True on success."""
    log = get_logger()
    token = settings.NOTION_TOKEN
    db_id = settings.NOTION_DATABASE_ID
    if not token or not db_id:
        log.debug("Notion not configured, skipping run notification")
        return False
    try:
        status = "Success" if success else "Failure"
        payload = {
            "parent": {"database_id": db_id},
            "properties": {
                "Name": {"title": [{"text": {"content": f"Pipeline Run {timestamp}"}}]},
                "Status": {"select": {"name": status}},
                "Items": {"number": item_count},
                "Report": {"url": report_path if report_path.startswith("http") else None},
            },
        }
        # Remove None url
        if payload["properties"]["Report"]["url"] is None:
            del payload["properties"]["Report"]

        resp = requests.post(
            "https://api.notion.com/v1/pages",
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": "2022-06-28",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        log.info("Notion run notification created")
        return True
    except Exception as exc:
        log.error("Notion run notification failed: %s", exc)
        return False


def send_all_notifications(
    timestamp: str, item_count: int, success: bool, report_path: str
) -> dict[str, bool]:
    """Fire all configured notification channels. Never raises."""
    results: dict[str, bool] = {}
    for channel_name, fn in [
        ("slack", notify_slack),
        ("email", notify_email),
        ("notion", notify_notion_run),
    ]:
        try:
            results[channel_name] = fn(timestamp, item_count, success, report_path)
        except Exception:
            results[channel_name] = False
    return results
