"""Centralized configuration loaded from .env with sensible defaults."""

import json
import os
from pathlib import Path

from dotenv import load_dotenv


def _env_int(key: str, default: int) -> int:
    """Read an env var as int; treat empty string as missing."""
    raw = os.getenv(key, "")
    return int(raw) if raw.strip() else default


def _env_float(key: str, default: float) -> float:
    """Read an env var as float; treat empty string as missing."""
    raw = os.getenv(key, "")
    return float(raw) if raw.strip() else default

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
NEWER_THAN_HOURS: int = _env_int("NEWER_THAN_HOURS", 24)
ALLOW_LANG: list[str] = [lang.strip() for lang in os.getenv("ALLOW_LANG", "zh,en").split(",") if lang.strip()]
KEYWORD_FILTER: list[str] = [k.strip() for k in os.getenv("KEYWORD_FILTER", "").split(",") if k.strip()]
MIN_BODY_LENGTH: int = _env_int("MIN_BODY_LENGTH", 120)
BATCH_SIZE: int = _env_int("BATCH_SIZE", 20)
CONTENT_GATE_MIN_KEEP_ITEMS: int = _env_int("CONTENT_GATE_MIN_KEEP_ITEMS", 12)
CONTENT_GATE_MIN_KEEP_SIGNALS: int = _env_int("CONTENT_GATE_MIN_KEEP_SIGNALS", 9)
CONTENT_GATE_STRICT_MIN_LEN: int = _env_int("CONTENT_GATE_STRICT_MIN_LEN", 1200)
CONTENT_GATE_STRICT_MIN_SENTENCES: int = _env_int("CONTENT_GATE_STRICT_MIN_SENTENCES", 3)
CONTENT_GATE_RELAXED_MIN_LEN: int = _env_int(
    "CONTENT_GATE_RELAXED_MIN_LEN",
    _env_int("CONTENT_GATE_SOFT_MIN_LEN", 500),
)
CONTENT_GATE_RELAXED_MIN_SENTENCES: int = _env_int("CONTENT_GATE_RELAXED_MIN_SENTENCES", 2)
EVENT_GATE_MIN_LEN: int = _env_int("EVENT_GATE_MIN_LEN", 1200)
EVENT_GATE_MIN_SENTENCES: int = _env_int("EVENT_GATE_MIN_SENTENCES", 3)
SIGNAL_GATE_MIN_LEN: int = _env_int("SIGNAL_GATE_MIN_LEN", 300)
SIGNAL_GATE_MIN_SENTENCES: int = _env_int("SIGNAL_GATE_MIN_SENTENCES", 2)

# Information-density gate (pre-content_strategy)
INFO_DENSITY_MIN_SCORE_EVENT: int = _env_int("INFO_DENSITY_MIN_SCORE_EVENT", 55)
INFO_DENSITY_MIN_ENTITY_EVENT: int = _env_int("INFO_DENSITY_MIN_ENTITY_EVENT", 2)
INFO_DENSITY_MIN_NUMERIC_EVENT: int = _env_int("INFO_DENSITY_MIN_NUMERIC_EVENT", 1)
INFO_DENSITY_MIN_SENTENCES_EVENT: int = _env_int("INFO_DENSITY_MIN_SENTENCES_EVENT", 3)

INFO_DENSITY_MIN_SCORE_SIGNAL: int = _env_int("INFO_DENSITY_MIN_SCORE_SIGNAL", 35)
INFO_DENSITY_MIN_ENTITY_SIGNAL: int = _env_int("INFO_DENSITY_MIN_ENTITY_SIGNAL", 1)
INFO_DENSITY_MIN_NUMERIC_SIGNAL: int = _env_int("INFO_DENSITY_MIN_NUMERIC_SIGNAL", 0)
INFO_DENSITY_MIN_SENTENCES_SIGNAL: int = _env_int("INFO_DENSITY_MIN_SENTENCES_SIGNAL", 2)

INFO_DENSITY_MIN_SCORE_CORP: int = _env_int("INFO_DENSITY_MIN_SCORE_CORP", 45)
INFO_DENSITY_MIN_ENTITY_CORP: int = _env_int("INFO_DENSITY_MIN_ENTITY_CORP", 1)
INFO_DENSITY_MIN_NUMERIC_CORP: int = _env_int("INFO_DENSITY_MIN_NUMERIC_CORP", 0)
INFO_DENSITY_MIN_SENTENCES_CORP: int = _env_int("INFO_DENSITY_MIN_SENTENCES_CORP", 2)

INFO_DENSITY_ENTITY_KEYWORDS: str = os.getenv("INFO_DENSITY_ENTITY_KEYWORDS", "")
INFO_DENSITY_BOILERPLATE_KEYWORDS: str = os.getenv("INFO_DENSITY_BOILERPLATE_KEYWORDS", "")

# ---------------------------------------------------------------------------
# Quality Gates
# ---------------------------------------------------------------------------
GATE_MIN_SCORE: float = _env_float("GATE_MIN_SCORE", 7.0)
GATE_MAX_DUP_RISK: float = _env_float("GATE_MAX_DUP_RISK", 0.25)

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
SLACK_WEBHOOK_URL: str = os.getenv("SLACK_WEBHOOK_URL", "")

# ---------------------------------------------------------------------------
# Email (SMTP)
# ---------------------------------------------------------------------------
SMTP_HOST: str = os.getenv("SMTP_HOST", "")
SMTP_PORT: int = _env_int("SMTP_PORT", 587)
SMTP_USER: str = os.getenv("SMTP_USER", "")
SMTP_PASS: str = os.getenv("SMTP_PASS", "")
ALERT_EMAIL: str = os.getenv("ALERT_EMAIL", "")

# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------
SCHEDULER_INTERVAL_SECONDS: int = _env_int("SCHEDULER_INTERVAL_SECONDS", 900)
SCHEDULER_CRON_HOUR: int = _env_int("SCHEDULER_CRON_HOUR", 9)
SCHEDULER_CRON_MINUTE: int = _env_int("SCHEDULER_CRON_MINUTE", 0)

# ---------------------------------------------------------------------------
# Run Profile (calibration vs prod)
# ---------------------------------------------------------------------------
RUN_PROFILE: str = os.getenv("RUN_PROFILE", "prod").strip().lower()

# Calibration overrides (only applied when RUN_PROFILE == "calibration")
_CALIBRATION_OVERRIDES = {
    "GATE_MIN_SCORE": 5.5,
    "MIN_BODY_LENGTH": 80,
    "NEWER_THAN_HOURS": 72,
    "GATE_MAX_DUP_RISK": 0.40,
}

if RUN_PROFILE == "calibration":
    GATE_MIN_SCORE = _env_float("GATE_MIN_SCORE", _CALIBRATION_OVERRIDES["GATE_MIN_SCORE"])
    MIN_BODY_LENGTH = _env_int("MIN_BODY_LENGTH", _CALIBRATION_OVERRIDES["MIN_BODY_LENGTH"])
    NEWER_THAN_HOURS = _env_int("NEWER_THAN_HOURS", _CALIBRATION_OVERRIDES["NEWER_THAN_HOURS"])
    GATE_MAX_DUP_RISK = _env_float("GATE_MAX_DUP_RISK", _CALIBRATION_OVERRIDES["GATE_MAX_DUP_RISK"])

# ---------------------------------------------------------------------------
# Deep Analysis (Z4)
# ---------------------------------------------------------------------------
DEEP_ANALYSIS_ENABLED: bool = os.getenv("DEEP_ANALYSIS_ENABLED", "true").strip().lower() in ("true", "1", "yes")
DEEP_ANALYSIS_OUTPUT_PATH: Path = _resolve(os.getenv("DEEP_ANALYSIS_OUTPUT_PATH", ""), r".\outputs\deep_analysis.md")

# ---------------------------------------------------------------------------
# Education Report (Z5)
# ---------------------------------------------------------------------------
EDU_REPORT_ENABLED: bool = os.getenv("EDU_REPORT_ENABLED", "true").strip().lower() in ("true", "1", "yes")
EDU_REPORT_MAX_ITEMS: int = _env_int("EDU_REPORT_MAX_ITEMS", 0)  # 0 = 不限制
EDU_REPORT_LANGUAGE: str = os.getenv("EDU_REPORT_LANGUAGE", "zh-TW")
EDU_REPORT_INCLUDE_MEDIA_PLACEHOLDERS: bool = os.getenv(
    "EDU_REPORT_INCLUDE_MEDIA_PLACEHOLDERS", "true"
).strip().lower() in ("true", "1", "yes")
EDU_REPORT_LEVEL: str = os.getenv("EDU_REPORT_LEVEL", "adult").strip().lower()  # adult | teen

# ---------------------------------------------------------------------------
# PPT Theme (light | dark)
# ---------------------------------------------------------------------------
PPT_THEME: str = os.getenv("PPT_THEME", "light").strip().lower()

# ---------------------------------------------------------------------------
# AI Topic Keywords — items must match at least one to pass content gate
# ---------------------------------------------------------------------------
_DEFAULT_AI_TOPIC_KEYWORDS = (
    "ai,llm,agent,model,inference,gpu,nvidia,openai,anthropic,google,microsoft,"
    "aws,meta,deepseek,qwen,rag,vector,vllm,transformer,multimodal,copilot,"
    "gemini,claude,gpt,chatgpt,llama,mistral,diffusion,neural,machine learning,"
    "deep learning,foundation model,generative,chatbot,langchain,cursor,bedrock,"
    "vertex,azure,huggingface,tensorflow,pytorch,算力,大模型,人工智慧,機器學習,"
    "深度學習,生成式,語言模型"
)
AI_TOPIC_KEYWORDS: list[str] = [
    k.strip().lower()
    for k in os.getenv("AI_TOPIC_KEYWORDS", _DEFAULT_AI_TOPIC_KEYWORDS).split(",")
    if k.strip()
]
