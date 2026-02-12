"""Z3 – SQLite persistence.

Tables: items, ai_results, dedup_cache.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from schemas.models import MergedResult, RawItem
from utils.logger import get_logger

# ---------------------------------------------------------------------------
# DB init
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS items (
    item_id     TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    url         TEXT NOT NULL,
    body        TEXT,
    published_at TEXT,
    source_name TEXT,
    source_category TEXT,
    lang        TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_results (
    item_id     TEXT PRIMARY KEY,
    schema_a    TEXT,
    schema_b    TEXT,
    schema_c    TEXT,
    passed_gate INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (item_id) REFERENCES items(item_id)
);

CREATE TABLE IF NOT EXISTS dedup_cache (
    item_id     TEXT PRIMARY KEY,
    title_hash  TEXT,
    url         TEXT,
    seen_at     TEXT NOT NULL
);
"""


def init_db(db_path: Path) -> None:
    """Create database tables if they don't exist."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(_DDL)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def get_connection(db_path: Path) -> sqlite3.Connection:
    """Get a SQLite connection with WAL mode for concurrency."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def get_existing_item_ids(db_path: Path) -> set[str]:
    """Return all item_ids already in the database (for dedup)."""
    conn = get_connection(db_path)
    try:
        rows = conn.execute("SELECT item_id FROM dedup_cache").fetchall()
        return {r["item_id"] for r in rows}
    finally:
        conn.close()


def save_items(db_path: Path, items: list[RawItem]) -> int:
    """Insert raw items into the items table. Returns count of new inserts."""
    log = get_logger()
    conn = get_connection(db_path)
    now = _now_iso()
    inserted = 0
    try:
        for item in items:
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO items
                       (item_id, title, url, body, published_at, source_name, source_category, lang, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        item.item_id,
                        item.title,
                        item.url,
                        item.body,
                        item.published_at,
                        item.source_name,
                        item.source_category,
                        item.lang,
                        now,
                    ),
                )
                # Also update dedup_cache
                conn.execute(
                    """INSERT OR IGNORE INTO dedup_cache (item_id, title_hash, url, seen_at)
                       VALUES (?, ?, ?, ?)""",
                    (item.item_id, item.title[:100], item.url, now),
                )
                inserted += 1
            except sqlite3.IntegrityError:
                pass
        conn.commit()
    finally:
        conn.close()
    log.info("Saved %d raw items to DB", inserted)
    return inserted


def save_results(db_path: Path, results: list[MergedResult]) -> int:
    """Insert AI results into the ai_results table. Returns count."""
    log = get_logger()
    conn = get_connection(db_path)
    now = _now_iso()
    saved = 0
    try:
        for r in results:
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO ai_results
                       (item_id, schema_a, schema_b, schema_c, passed_gate, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        r.item_id,
                        json.dumps(r.schema_a.to_dict(), ensure_ascii=False),
                        json.dumps(r.schema_b.to_dict(), ensure_ascii=False),
                        json.dumps(r.schema_c.to_dict(), ensure_ascii=False),
                        1 if r.passed_gate else 0,
                        now,
                    ),
                )
                saved += 1
            except Exception as exc:
                log.error("Failed to save result %s: %s", r.item_id, exc)
        conn.commit()
    finally:
        conn.close()
    log.info("Saved %d AI results to DB", saved)
    return saved


def load_passed_results(db_path: Path, limit: int = 50) -> list[dict]:
    """Load recent results that passed quality gates."""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            """SELECT r.item_id, r.schema_a, r.schema_b, r.schema_c, r.created_at,
                      i.title, i.url, i.source_name
               FROM ai_results r
               JOIN items i ON r.item_id = i.item_id
               WHERE r.passed_gate = 1
               ORDER BY r.created_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        results = []
        for row in rows:
            results.append(
                {
                    "item_id": row["item_id"],
                    "schema_a": json.loads(row["schema_a"]) if row["schema_a"] else {},
                    "schema_b": json.loads(row["schema_b"]) if row["schema_b"] else {},
                    "schema_c": json.loads(row["schema_c"]) if row["schema_c"] else {},
                    "created_at": row["created_at"],
                    "title": row["title"],
                    "url": row["url"],
                    "source_name": row["source_name"],
                }
            )
        return results
    finally:
        conn.close()
