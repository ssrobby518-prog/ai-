"""Centralized configuration loaded from .env with sensible defaults."""

import json
import os
from pathlib import Path

from dotenv import load_dotenv

# Project root is the parent of config/
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# ---------------------------------------------------------------------------
# Paths (resolved relative to PROJECT_ROOT when relative)
# ---------------------------------------------------------------------------


def _resolve(raw: str, default: str) -> Path:
    p = Path(raw) if raw else Path(default)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


DB_PATH: Path = _resolve(os.getenv("DB_PATH", ""), r".\data\intel.db")
OUTPUT_DIGEST_PATH: Path = _resolve(os.getenv("OUTPUT_DIGEST_PATH", ""), r".\outputs\digest.md")
LOG_PATH: Path = _resolve(os.getenv("LOG_PATH", ""), r".\logs\app.log")

# ---------------------------------------------------------------------------
# RSS Feeds
# ---------------------------------------------------------------------------
_DEFAULT_FEEDS = json.dumps(
    [
        {"name": "36kr", "url": "https://36kr.com/feed", "lang": "zh", "category": "tech"},
        {"name": "HackerNews", "url": "https://hnrss.org/newest?points=50", "lang": "en", "category": "tech"},
        {"name": "TechCrunch", "url": "https://techcrunch.com/feed", "lang": "en", "category": "startup"},
    ]
)

RSS_FEEDS: list[dict] = json.loads(os.getenv("RSS_FEEDS_JSON", _DEFAULT_FEEDS))

# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
NEWER_THAN_HOURS: int = int(os.getenv("NEWER_THAN_HOURS", "24"))
ALLOW_LANG: list[str] = [lang.strip() for lang in os.getenv("ALLOW_LANG", "zh,en").split(",") if lang.strip()]
KEYWORD_FILTER: list[str] = [k.strip() for k in os.getenv("KEYWORD_FILTER", "").split(",") if k.strip()]
MIN_BODY_LENGTH: int = int(os.getenv("MIN_BODY_LENGTH", "120"))
BATCH_SIZE: int = int(os.getenv("BATCH_SIZE", "20"))

# ---------------------------------------------------------------------------
# Quality Gates
# ---------------------------------------------------------------------------
GATE_MIN_SCORE: float = float(os.getenv("GATE_MIN_SCORE", "7.0"))
GATE_MAX_DUP_RISK: float = float(os.getenv("GATE_MAX_DUP_RISK", "0.25"))

# ---------------------------------------------------------------------------
# LLM Provider
# ---------------------------------------------------------------------------
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "none").strip().lower()
LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "")
LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
LLM_MODEL: str = os.getenv("LLM_MODEL", "deepseek-chat")

# ---------------------------------------------------------------------------
# Optional Sinks
# ---------------------------------------------------------------------------
NOTION_TOKEN: str = os.getenv("NOTION_TOKEN", "")
NOTION_DATABASE_ID: str = os.getenv("NOTION_DATABASE_ID", "")
FEISHU_WEBHOOK_URL: str = os.getenv("FEISHU_WEBHOOK_URL", "")

# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------
SCHEDULER_INTERVAL_SECONDS: int = int(os.getenv("SCHEDULER_INTERVAL_SECONDS", "900"))

# ---------------------------------------------------------------------------
# Deep Analysis (Z4)
# ---------------------------------------------------------------------------
DEEP_ANALYSIS_ENABLED: bool = os.getenv("DEEP_ANALYSIS_ENABLED", "true").strip().lower() in ("true", "1", "yes")
DEEP_ANALYSIS_OUTPUT_PATH: Path = _resolve(os.getenv("DEEP_ANALYSIS_OUTPUT_PATH", ""), r".\outputs\deep_analysis.md")
