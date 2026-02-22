# FILE: main.py
import asyncio
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from llm_engine import LlamaCppServer, generate_bbc_news
from scraper import scrape_all

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent
DB_PATH = REPO_ROOT / "data" / "intel.db"
OUTPUT_DIR = REPO_ROOT / "outputs"


def _init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS intel (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            source      TEXT NOT NULL,
            bbc_summary TEXT NOT NULL,
            created_at  TEXT NOT NULL
        )
        """
    )
    conn.commit()


def _save_record(conn: sqlite3.Connection, source: str, bbc_summary: str) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        "INSERT INTO intel (source, bbc_summary, created_at) VALUES (?, ?, ?)",
        (source, bbc_summary, now),
    )
    conn.commit()


async def main() -> None:
    server = LlamaCppServer()
    conn = None
    try:
        logger.info("Starting scrape...")
        items = await scrape_all()
        filtered = [it for it in items if len(it.get("raw_text", "")) >= 50]
        logger.info(
            "Scraped %d items total, %d passed length filter.", len(items), len(filtered)
        )

        if not filtered:
            logger.info("No items to process. Exiting.")
            return

        await server.start()

        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH))
        _init_db(conn)

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        today = datetime.now().strftime("%Y%m%d")
        report_path = OUTPUT_DIR / f"report_{today}.md"

        report_lines = [f"# AI Intel Report — {today}\n"]

        for idx, item in enumerate(filtered, 1):
            source = item["source"]
            raw_text = item["raw_text"]
            published_at = item.get("published_at") or item.get("collected_at", today[:4] + "-" + today[4:6] + "-" + today[6:])
            logger.info(
                "[%d/%d] Generating LLM summary for: %s (%s)",
                idx, len(filtered), source, published_at,
            )
            try:
                summary = await generate_bbc_news(raw_text, source, published_at)
                _save_record(conn, source, summary)
                report_lines.append(f"## {source}  ({published_at})\n")
                report_lines.append(f"{summary}\n")
                report_lines.append("---\n")
            except Exception as exc:
                logger.error("LLM failed for source '%s': %s", source, exc)

        report_path.write_text("\n".join(report_lines), encoding="utf-8")
        logger.info("Report written to: %s", report_path)

    finally:
        if conn is not None:
            conn.close()
        server.stop()


if __name__ == "__main__":
    asyncio.run(main())
