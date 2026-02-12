"""Z3 – Delivery sinks.

- Local: outputs/digest.md + console summary
- Optional: Notion database upsert
- Optional: Feishu webhook push
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import requests
from config import settings
from schemas.models import MergedResult
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
from utils.logger import get_logger

# ---------------------------------------------------------------------------
# Local sink: digest.md
# ---------------------------------------------------------------------------


def write_digest(results: list[MergedResult], output_path: Path | None = None) -> Path:
    """Generate digest.md from results that passed quality gates."""
    log = get_logger()
    path = output_path or settings.OUTPUT_DIGEST_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    passed = [r for r in results if r.passed_gate]
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    lines: list[str] = [
        "# AI 情報報告",
        "",
        f"生成時間: {now}",
        "",
        f"總處理筆數: {len(results)} | 通過門檻: {len(passed)}",
        "",
        "---",
        "",
    ]

    if not passed:
        lines.append("*本次無項目通過品質門檻。*\n")
    else:
        for i, r in enumerate(passed, 1):
            a = r.schema_a
            b = r.schema_b
            title = a.title_zh or "(無標題)"
            summary = a.summary_zh or "(無摘要)"
            score = f"{b.final_score:.1f}"
            tags = ", ".join(b.tags) if b.tags else "-"
            entities = ", ".join(a.entities) if a.entities else "-"
            key_points_md = "\n".join(f"  - {kp}" for kp in a.key_points) if a.key_points else "  - (無)"

            lines.extend(
                [
                    f"## {i}. {title}",
                    "",
                    f"- **分數**: {score} | **分類**: {a.category} | **來源**: {a.source_id}",
                    f"- **標籤**: {tags}",
                    f"- **實體**: {entities}",
                    "- **重點**:",
                    key_points_md,
                    "",
                    f"> {summary}",
                    "",
                    "---",
                    "",
                ]
            )

    content = "\n".join(lines)
    path.write_text(content, encoding="utf-8")
    log.info("Digest written to %s (%d passed items)", path, len(passed))
    return path


def print_console_summary(results: list[MergedResult]) -> None:
    """Print a compact summary table to console."""
    log = get_logger()
    passed = [r for r in results if r.passed_gate]
    total = len(results)

    log.info("=" * 60)
    log.info("流程摘要")
    log.info("=" * 60)
    log.info("總處理筆數: %d", total)
    log.info("通過門檻筆數: %d", len(passed))
    log.info("-" * 60)

    for r in passed:
        title = (r.schema_a.title_zh or "(無標題)")[:50]
        log.info(
            "  [%.1f] %s (%s)",
            r.schema_b.final_score,
            title,
            ", ".join(r.schema_b.tags[:3]),
        )

    log.info("=" * 60)


# ---------------------------------------------------------------------------
# Optional: Notion sink
# ---------------------------------------------------------------------------


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(requests.RequestException),
    reraise=True,
)
def _notion_create_page(token: str, database_id: str, properties: dict) -> dict:
    """Create a page in a Notion database."""
    url = "https://api.notion.com/v1/pages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }
    payload = {
        "parent": {"database_id": database_id},
        "properties": properties,
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def push_to_notion(results: list[MergedResult]) -> int:
    """Upsert passed results to Notion. Returns count of pushed items."""
    log = get_logger()
    token = settings.NOTION_TOKEN
    db_id = settings.NOTION_DATABASE_ID

    if not token or not db_id:
        log.debug("Notion sink not configured, skipping")
        return 0

    passed = [r for r in results if r.passed_gate]
    pushed = 0

    for r in passed:
        try:
            properties = {
                "Title": {"title": [{"text": {"content": r.schema_a.title_zh or r.item_id}}]},
                "Score": {"number": r.schema_b.final_score},
                "Category": {"select": {"name": r.schema_a.category or "general"}},
                "Source": {"select": {"name": r.schema_a.source_id or "unknown"}},
                "Tags": {"multi_select": [{"name": t} for t in r.schema_b.tags[:5]]},
                "Summary": {"rich_text": [{"text": {"content": (r.schema_a.summary_zh or "")[:2000]}}]},
                "URL": {"url": r.schema_c.cta_url or None},
            }
            _notion_create_page(token, db_id, properties)
            pushed += 1
        except Exception as exc:
            log.error("Notion push failed for %s: %s", r.item_id, exc)

    log.info("Notion: pushed %d / %d items", pushed, len(passed))
    return pushed


# ---------------------------------------------------------------------------
# Optional: Feishu webhook
# ---------------------------------------------------------------------------


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(requests.RequestException),
    reraise=True,
)
def _feishu_send(webhook_url: str, payload: dict) -> dict:
    """Send a message to Feishu via webhook."""
    resp = requests.post(webhook_url, json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()


def push_to_feishu(results: list[MergedResult]) -> int:
    """Push passed results to Feishu as card messages. Returns count."""
    log = get_logger()
    webhook = settings.FEISHU_WEBHOOK_URL

    if not webhook:
        log.debug("Feishu sink not configured, skipping")
        return 0

    passed = [r for r in results if r.passed_gate]
    pushed = 0

    for r in passed:
        try:
            card_content = {
                "msg_type": "interactive",
                "card": {
                    "header": {
                        "title": {
                            "tag": "plain_text",
                            "content": r.schema_c.title or r.schema_a.title_zh or "Intel Update",
                        },
                        "template": "blue",
                    },
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": r.schema_c.card_md or r.schema_a.summary_zh or "",
                        },
                        {
                            "tag": "action",
                            "actions": [
                                {
                                    "tag": "button",
                                    "text": {"tag": "plain_text", "content": "查看原文"},
                                    "url": r.schema_c.cta_url or "",
                                    "type": "primary",
                                },
                            ],
                        },
                    ],
                },
            }
            _feishu_send(webhook, card_content)
            pushed += 1
        except Exception as exc:
            log.error("Feishu push failed for %s: %s", r.item_id, exc)

    log.info("Feishu: pushed %d / %d items", pushed, len(passed))
    return pushed
