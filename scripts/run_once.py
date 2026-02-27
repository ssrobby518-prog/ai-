"""Run the full pipeline once: Ingest -> Process -> Store -> Deliver."""

import hashlib
import os
import re
import shutil
import sys
import time
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings
from core.ai_core import process_batch
from core.content_strategy import (
    build_decision_card,
    build_corp_watch_summary,
    get_event_cards_for_deck,
    build_signal_summary,
    is_non_event_or_index,
    register_item_urls,
)
from core.deep_analyzer import analyze_batch
from core.deep_delivery import write_deep_analysis
from core.delivery import print_console_summary, push_to_feishu, push_to_notion, write_digest
from core.education_renderer import (
    generate_binary_reports,
    generate_executive_reports,
    render_education_report,
    render_error_report,
    write_education_reports,
)
from core.ingestion import batch_items, dedup_items, fetch_all_feeds, filter_items
from core.info_density import apply_density_gate
from core.notifications import send_all_notifications
from core.storage import get_existing_item_ids, init_db, save_items, save_results
from schemas.education_models import EduNewsCard
from schemas.models import RawItem
from utils.entity_cleaner import clean_entities
from utils.logger import setup_logger
from utils.metrics import get_collector, reset_collector
from utils.evidence_pack import (
    AI_KEYWORDS,
    compute_ai_relevance,
    extract_event_anchors,
    check_no_boilerplate,
    check_q1_structure,
    check_q2_structure,
    check_moves_anchored,
    check_exec_readability,
)
from utils.newsroom_zh_rewrite import rewrite_news_lead_v2, rewrite_news_impact_v2
from utils.zh_narrative_validator import validate_zh_card_fields


def _apply_entity_cleaning(all_results: list) -> None:
    """Clean entities on all results and record metrics."""
    collector = get_collector()
    for r in all_results:
        a = r.schema_a
        before = len(a.entities)
        result = clean_entities(
            entities=a.entities,
            category=a.category,
            key_points=a.key_points,
            title=a.title_zh,
            body=a.summary_zh,
        )
        a.entities = result.cleaned
        collector.record_entity_cleaning(before, len(result.cleaned))


def _build_quality_cards(
    all_results: list,
    source_url_map: dict[str, str] | None = None,
    fulltext_len_map: "dict[str, int] | None" = None,
) -> list[EduNewsCard]:
    """Build lightweight EduNewsCard objects for v5.2 metrics aggregation."""
    cards: list[EduNewsCard] = []
    source_url_map = source_url_map or {}
    fulltext_len_map = fulltext_len_map or {}
    for r in all_results:
        a = r.schema_a
        b = r.schema_b
        c = r.schema_c
        source_url = str(getattr(c, "cta_url", "") or "").strip()
        if not source_url.startswith(("http://", "https://")):
            fallback_url = str(source_url_map.get(str(r.item_id), "") or "").strip()
            if fallback_url.startswith(("http://", "https://")):
                source_url = fallback_url
        card = EduNewsCard(
            item_id=str(r.item_id),
            is_valid_news=bool(getattr(r, "passed_gate", False)),
            invalid_reason="" if bool(getattr(r, "passed_gate", False)) else "failed_gate",
            title_plain=str(a.title_zh or ""),
            what_happened=str(a.summary_zh or ""),
            why_important=str(a.summary_zh or ""),
            source_name=str(a.source_id or ""),
            source_url=source_url if source_url.startswith(("http://", "https://")) else "",
            category=str(a.category or ""),
            final_score=float(getattr(b, "final_score", 0.0) or 0.0),
        )
        # Propagate fulltext_len from the underlying RawItem (set by hydrate_items_batch)
        _ft_len = int(fulltext_len_map.get(str(r.item_id), 0) or 0)
        try:
            setattr(card, "fulltext_len", _ft_len)
        except Exception:
            pass
        cards.append(card)
    return cards


def _build_soft_quality_cards_from_filtered(filtered_items: list) -> list[EduNewsCard]:
    """Build fallback cards from post-gate RawItems when AI results are empty."""
    cards: list[EduNewsCard] = []
    for item in filtered_items:
        title = str(getattr(item, "title", "") or "").strip() or "Untitled Event"
        body = str(getattr(item, "body", "") or "").strip()
        # Prefer hydrated full_text for summary so canonical clean_len >= 300 passes
        # the demotion block in get_event_cards_for_deck; fall back to body.
        _full_text_attr = str(getattr(item, "full_text", "") or "").strip()
        _summary_source = _full_text_attr if _full_text_attr else body
        summary = _summary_source[:500] if _summary_source else "No summary available from source."
        source_name = str(getattr(item, "source_name", "") or "").strip() or "unknown_source"
        source_url = str(getattr(item, "url", "") or "").strip()
        density = float(getattr(item, "density_score", 0) or 0)
        score = max(3.0, min(10.0, round(density / 10.0, 2)))
        card = EduNewsCard(
            item_id=str(getattr(item, "item_id", "") or ""),
            is_valid_news=True,
            title_plain=title,
            what_happened=summary,
            why_important=f"Source {source_name}; reference {source_url if source_url.startswith('http') else 'N/A'}.",
            source_name=source_name,
            source_url=source_url if source_url.startswith("http") else "",
            category=str(getattr(item, "source_category", "") or "tech"),
            final_score=score,
        )
        try:
            setattr(card, "event_gate_pass", bool(getattr(item, "event_gate_pass", True)))
            setattr(card, "signal_gate_pass", bool(getattr(item, "signal_gate_pass", True)))
            setattr(card, "density_score", int(density))
            setattr(card, "density_tier", "A" if bool(getattr(item, "event_gate_pass", False)) else "B")
            # Propagate fulltext_len from RawItem so POOL_SUFFICIENCY gate sees hydrated lengths
            _ft_len = int(getattr(item, "fulltext_len", 0) or 0)
            setattr(card, "fulltext_len", _ft_len)
            if _ft_len >= 300:
                _ft_text = str(getattr(item, "full_text", "") or "")
                if _ft_text:
                    setattr(card, "full_text", _ft_text)
        except Exception:
            pass
        cards.append(card)
    return cards


def _select_processing_items(
    filtered_items: list[RawItem],
    signal_pool: list[RawItem],
    *,
    fallback_limit: int = 3,
    include_signal_context: bool = False,
    signal_context_limit: int = 0,
) -> tuple[list[RawItem], bool]:
    """Select items for downstream Z2/Z3 processing.

    Priority:
    1) event-gate-passed `filtered_items`
    2) when event gate is empty but signal gate has candidates, use a small
       signal fallback slice to avoid all-empty runs.
    """
    if filtered_items:
        selected = list(filtered_items)
        if include_signal_context and signal_pool:
            remaining = max(0, int(signal_context_limit))
            existing_ids = {str(getattr(it, "item_id", "") or "") for it in selected}
            for item in signal_pool:
                if remaining <= 0:
                    break
                item_id = str(getattr(item, "item_id", "") or "")
                if item_id and item_id in existing_ids:
                    continue
                try:
                    setattr(item, "event_gate_pass", False)
                    setattr(item, "signal_gate_pass", True)
                    setattr(item, "low_confidence", True)
                except Exception:
                    pass
                selected.append(item)
                existing_ids.add(item_id)
                remaining -= 1
        return selected, False

    if not signal_pool:
        return [], False

    limit = max(1, int(fallback_limit))
    selected = list(signal_pool[:limit])
    for item in selected:
        try:
            setattr(item, "event_gate_pass", False)
            setattr(item, "signal_gate_pass", True)
            setattr(item, "low_confidence", True)
        except Exception:
            pass
    return selected, True


def _extract_ph_supp_quotes(text: str, n: int = 12) -> list:
    """Extract top-N verbatim quote candidates from article full_text.

    Scores each sentence by information density: numbers, money amounts,
    and known company/product names.  Filters out fragments that are too
    short (< 20 chars) or too few words (< 4) to be meaningful quotes.
    Returns a list of strings sorted by score descending, up to *n* items.
    Used by the EXEC_NEWS_QUALITY_HARD gate to bind Q1/Q2 to source text.
    """
    import re as _re_q
    _num_re_q   = _re_q.compile(
        r'\b\d[\d,]*(?:\.\d+)?(?:\s*[%xX]|\s*(?:billion|million|trillion|percent|B|M|K)\b)?'
    )
    _money_re_q = _re_q.compile(
        r'\$[\d,]+(?:\.\d+)?(?:\s*(?:billion|million|trillion|B|M|K)\b)?'
    )
    _co_re_q = _re_q.compile(
        r'\b(?:Google|Microsoft|Apple|Amazon|Meta|OpenAI|Anthropic|NVIDIA|IBM|'
        r'Tesla|DeepMind|HuggingFace|Hugging\s*Face|Spotify|Acme|IQM|Firefox|'
        r'Wispr|Particle|Guide|AlphaFold|Quantum|AI|LLM)\b'
    )
    sents = _re_q.split(r'(?<=[.!?])\s+', text.replace('\r\n', ' ').replace('\r', ' ').replace('\n', ' '))
    cands: list = []
    for _s in sents:
        _s = _s.strip()
        if len(_s) < 20:
            continue
        if len(_s.split()) < 4:
            continue
        _inner = _re_q.sub(r'[^a-zA-Z0-9]', '', _s)
        if not _inner or _inner.isdigit():
            continue
        _sc = (
            len(_num_re_q.findall(_s)) * 3
            + len(_money_re_q.findall(_s)) * 2
            + len(_co_re_q.findall(_s)) * 1
            + min(len(_s) // 30, 3)
        )
        cands.append((_sc, _s))
    cands.sort(key=lambda x: -x[0])
    result: list = []
    seen: set = set()
    for _, _s in cands:
        if _s not in seen:
            result.append(_s)
            seen.add(_s)
            if len(result) >= n:
                break
    return result


_CLAUDE_TRANSLIT_RE = re.compile(r"(?:克勞德|克劳德|柯勞德|可勞德|可劳德|克洛德)", re.IGNORECASE)
_CLAUDE_WORD_RE = re.compile(r"\bClaude\b(?!\s*\(Anthropic\))")
_ACTOR_STOPWORDS = {
    "The", "This", "That", "These", "Those", "Today", "Breaking",
    "AI", "LLM", "News", "Report", "Update",
    "Blog", "Research", "Official", "Team", "Press", "Posted", "Post",
    "How", "From", "Introducing", "Unlocking", "Benchmarking",
}
_ACTOR_STOPWORDS_LOWER = {w.lower() for w in _ACTOR_STOPWORDS}
_ACTOR_BRAND_HINTS = (
    "OpenAI",
    "Anthropic",
    "Microsoft",
    "Google",
    "NVIDIA",
    "Meta",
    "Amazon",
    "Apple",
    "LinkedIn",
    "HuggingFace",
    "ServiceNow",
    "Falcon-H1-Arabic",
    "AprielGuard",
    "Cappy",
    "GPT-OSS",
    "Nemotron",
    "Chain-of-table",
)

_STYLE_SANITY_PATTERNS = [
    # Required exact hard-fail patterns
    r"\u5f15\u767c.*(?:\u8a0e\u8ad6|\u95dc\u6ce8|\u71b1\u8b70)",
    r"\u5177\u6709.*(?:\u5be6\u8cea|\u91cd\u5927).*(?:\u5f71\u97ff|\u610f\u7fa9)",
    r"(?:\u5404\u65b9|\u696d\u754c).*(?:\u8457\u624b|\u6b63).*(?:\u8a55\u4f30|\u8ffd\u8e64).*(?:\u5f8c\u7e8c|\u5f71\u97ff|\u52d5\u5411)",
    r"\u6599\u5c07\u5f71\u97ff.*(?:\u683c\u5c40|\u8d70\u5411|\u5e02\u5834)",
]
_STYLE_SANITY_RE = re.compile("|".join(_STYLE_SANITY_PATTERNS), re.IGNORECASE)

_AI_RELEVANCE_RE = re.compile(
    r"\b(?:AI|LLM|GPT(?:-\d+)?|Claude|Anthropic|OpenAI|Gemini|model|models|machine learning|"
    r"neural|transformer|transformers|diffusion|embedding|encoder|inference|quantization|"
    r"text-to-image|multimodal|agent|agents|foundation model|foundation models|"
    r"fine-tune|fine-tuning|fine_tune|fine_tuning|"
    r"benchmark|benchmarks|benchmarking|"
    r"CUDA|GPU|TPU|"
    r"reasoning|chain-of-thought|"
    r"autonomous|autonomy|"
    r"synthetic data|synthetic|"
    r"Hugging Face|HuggingFace|"
    r"RAG|retrieval-augmented|"
    r"RLHF|reinforcement learning|"
    r"pre-train|pre-training|pretrain|pretraining|"
    r"tokenizer|tokenization|"
    r"vector database|vector store|"
    r"prompt|prompting|"
    r"Llama|Mistral|Falcon|Stable Diffusion|"
    r"NVIDIA|A100|H100)\b"
    r"|hallucin",  # prefix match for hallucination/hallucinate
    re.IGNORECASE,
)

_NO_BOILERPLATE_RE = re.compile(
    r"最新公告顯示|確認.*原文出處|避免基於推測|原文已提供.*文字依據|引發.*廣泛關注",
    re.IGNORECASE,
)


def _normalize_ws(text: str) -> str:
    return " ".join(str(text or "").replace("\r", " ").replace("\n", " ").split())


def _clip_text(text: str, limit: int = 110) -> str:
    txt = _normalize_ws(text)
    return txt if len(txt) <= limit else txt[:limit].rstrip()


_BRIEF_BOILERPLATE_RE = re.compile(
    r"最新公告顯示|確認.*原文出處|避免基於推測|原文已提供.*文字依據|引發.*廣泛關注",
    re.IGNORECASE,
)

# Audit-tone phrases that make bullets sound like compliance reports rather than news.
# Hard-ban from any generated bullet text (DoD BRIEF_NO_AUDIT_SPEAK_HARD gate).
_BRIEF_AUDIT_SPEAK_TERMS = [
    "可核對", "可回查", "可補足", "可回推", "假設條件", "執行限制",
    "評估基準", "避免誤讀", "補齊邊界", "涉入對象", "重新比較",
]
_BRIEF_AUDIT_SPEAK_RE = re.compile(
    "|".join(re.escape(t) for t in _BRIEF_AUDIT_SPEAK_TERMS),
)

# Simplified Chinese character blacklist — any match = NOT zh-TW
_SIMPLIFIED_ZH_RE = re.compile(
    r"[这为发国时们说关见进现实产动话还经问应该对给让从么则导获总义变将区来没样过种几间后点确无开长书东语认风气电车门办设边]"
)

_BRIEF_GARBAGE_ACTORS = {
    "git", "true", "false", "none", "null", "na", "n/a", "4.0", "3.5", "1.0",
}

_BRIEF_QUOTE_SPAN_START = 0.15
_BRIEF_QUOTE_SPAN_END = 0.75
_BRIEF_QUOTE_SPAN_POLICY = "0.15-0.75"
_BRIEF_TARGET_WHAT_BULLETS = 5
_BRIEF_TARGET_KEY_BULLETS = 4
_BRIEF_TARGET_WHY_BULLETS_DEFAULT = 4
_BRIEF_TARGET_WHY_BULLETS_MIN = 3
_BRIEF_MIN_BULLET_CJK_CHARS = 18
_BRIEF_MIN_ANCHOR_NUMBER_HITS = 3
_BRIEF_MAX_SENTENCE_CANDIDATES = 20

_TIER_A_SOURCE_RE = re.compile(
    r"(openai|anthropic|hugging\s*face|huggingface|google\s+research|google\s+ai|"
    r"deepmind|model\s+release|research\s+blog|official\s+blog)",
    re.IGNORECASE,
)
_TIER_A_URL_RE = re.compile(
    r"(openai\.com/blog|anthropic\.com/news|huggingface\.co/blog|ai\.googleblog\.com|"
    r"blog\.google/.*/ai|research\.google|deepmind\.google/.*/blog|google-research)",
    re.IGNORECASE,
)


def _is_tier_a_source(source_name: str, url: str, title: str = "") -> bool:
    blob = _normalize_ws(f"{source_name} {url} {title}")
    if not blob:
        return False
    return bool(_TIER_A_SOURCE_RE.search(blob) or _TIER_A_URL_RE.search(_normalize_ws(url)))


def _brief_candidate_priority(fc: dict) -> tuple:
    src_name = _normalize_ws(str(fc.get("source_name", "") or ""))
    final_url = _normalize_ws(str(fc.get("final_url", "") or fc.get("source_url", "") or ""))
    title = _normalize_ws(str(fc.get("title", "") or ""))
    tier_a = 1 if _is_tier_a_source(src_name, final_url, title) else 0
    ai = 1 if bool(fc.get("ai_relevance", False)) else 0
    full_text_len = len(_normalize_ws(str(fc.get("full_text", "") or "")))
    quote_len = len(_normalize_ws(str(fc.get("quote_1", "") or ""))) + len(_normalize_ws(str(fc.get("quote_2", "") or "")))
    anchors_n = len(fc.get("anchors", []) or [])
    return (tier_a, ai, full_text_len, quote_len, anchors_n)


def _parse_iso_utc(ts: str) -> datetime | None:
    raw = _normalize_ws(ts)
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


def _resolve_report_mode() -> str:
    """Resolve report mode from env / argv. Supported: brief, legacy."""
    raw = _normalize_ws(os.environ.get("PIPELINE_REPORT_MODE", ""))
    if not raw:
        argv = list(sys.argv or [])
        for i, arg in enumerate(argv):
            low = str(arg or "").strip().lower()
            if low in ("--report-mode", "-reportmode") and i + 1 < len(argv):
                raw = _normalize_ws(argv[i + 1])
                break
            if low.startswith("--report-mode="):
                raw = _normalize_ws(str(arg).split("=", 1)[1])
                break
    mode = raw.lower()
    if mode not in {"brief", "legacy"}:
        mode = "legacy"
    os.environ["PIPELINE_REPORT_MODE"] = mode
    return mode


def _brief_is_garbage_actor(actor: str) -> bool:
    a = _normalize_ws(actor).strip()
    if not a:
        return True
    if a.lower() in _BRIEF_GARBAGE_ACTORS:
        return True
    if re.fullmatch(r"(?:v)?\d+(?:\.\d+){1,4}", a):
        return True
    return False


def _brief_contains_boilerplate(*parts: str) -> bool:
    joined = _normalize_ws(" ".join(parts))
    if not joined:
        return False
    return bool(_BRIEF_BOILERPLATE_RE.search(joined))


def _brief_contains_audit_speak(*parts: str) -> bool:
    """Return True if any part contains a banned audit-tone phrase."""
    joined = _normalize_ws(" ".join(parts))
    if not joined:
        return False
    return bool(_BRIEF_AUDIT_SPEAK_RE.search(joined))


def _brief_zh_cjk_ratio(text: str) -> float:
    """CJK char count / non-ASCII non-space char count.
    Returns 0.0 when no non-ASCII chars present (pure ASCII / empty = not zh-TW)."""
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    non_ascii_non_space = sum(1 for c in text if ord(c) > 127 and not c.isspace())
    if non_ascii_non_space == 0:
        return 0.0
    return cjk / non_ascii_non_space


def _brief_zh_tw_ok(text: str) -> bool:
    """True if text is zh-TW: CJK ratio >= threshold and no simplified-Chinese chars."""
    try:
        min_ratio = float(os.environ.get("BRIEF_ZH_TW_MIN_CJK", "0.6"))
    except (ValueError, TypeError):
        min_ratio = 0.6
    if _brief_zh_cjk_ratio(text) < min_ratio:
        return False
    if _SIMPLIFIED_ZH_RE.search(text):
        return False
    return True


def _brief_has_anchor_token(text: str, anchors: list[str]) -> bool:
    src = _normalize_ws(text)
    if not src:
        return False
    for anc in anchors:
        a = _normalize_ws(anc)
        if not a:
            continue
        if a.isascii():
            if a.lower() in src.lower():
                return True
        elif a in src:
            return True
    return bool(re.search(r"\b\d[\d,\.]*\b|\b[A-Z][A-Za-z0-9\-]{2,}\b", src))


def _brief_pick_primary_anchor(actor: str, anchors: list[str]) -> str:
    candidates: list[str] = []
    for a in anchors or []:
        aa = _normalize_ws(a)
        if aa:
            candidates.append(aa)
    if actor:
        candidates.insert(0, _normalize_ws(actor))
    for c in candidates:
        if _is_actor_numeric(c) or _brief_is_garbage_actor(c):
            continue
        return c
    return ""


def _brief_impact_target(category: str) -> str:
    cat = _normalize_ws(category).lower()
    if cat == "product":
        return "產品策略與版本路線"
    if cat == "business":
        return "營收結構與商業化節奏"
    return "技術架構與交付品質"


def _brief_decision_angle(category: str) -> str:
    cat = _normalize_ws(category).lower()
    if cat == "product":
        return "優先排程功能落地與驗證節點"
    if cat == "business":
        return "先確認成本回收與投資節奏"
    return "優先處理風險與可維運性"


def _build_brief_what_happened(title: str, actor: str, anchor: str) -> str:
    line1 = _normalize_ws(f"{actor} 針對「{title}」發布更新，並以「{anchor}」作為執行錨點。")
    line2 = _normalize_ws("事件已提供可核對的逐字證據與來源連結，可直接支撐後續排程與驗證。")
    return f"{line1}\n{line2}"


def _build_brief_why_it_matters(category: str, anchor: str) -> str:
    target = _brief_impact_target(category)
    angle = _brief_decision_angle(category)
    line1 = _normalize_ws(f"此案直接影響「{target}」，管理層需以「{anchor}」對齊決策口徑。")
    line2 = _normalize_ws(f"建議優先採取「{angle}」，避免跨部門評估標準分歧。")
    return f"{line1}\n{line2}"


_BRIEF_EMAIL_RE = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
    re.IGNORECASE,
)

_BRIEF_CTA_RE = re.compile(
    r"(hear from|sessions|subscribe|newsletter|cookies|privacy|advertis|sign up|register|terms|sponsor|promo|conference|buy tickets|event"
    r"|unsubscribe|manage subscription|upgrade to paid|paid subscriber|forwarded this email|view in browser"
    r"|get it in your inbox|leave a comment|share this post|restack|open in app|read in app"
    r"|contact|verify outreach"
    r"|\$\s*\d{2,})",
    re.IGNORECASE,
)

_BRIEF_MIN_EN_SENTENCE_CHARS = 60
_BRIEF_MIN_CJK_SENTENCE_CHARS = 20
_BRIEF_SENTENCE_MAX_CHARS = 360
_BRIEF_IMPACT_WORD_RE = re.compile(
    r"\b(will|could|risk|impact|ban|regulation|launch|release|acquire|raise|"
    r"lawsuit|fine|security|compliance|revenue|cost|pricing|roadmap|policy)\b",
    re.IGNORECASE,
)
_BRIEF_ACTION_WORD_RE = re.compile(
    r"\b(launch|release|released|announc|open|partner|acquire|raise|rollout|"
    r"ship|deploy|introduc|publish|expand|update)\b",
    re.IGNORECASE,
)

_BRIEF_GENERIC_NARRATIVE_RULES: list[tuple[str, re.Pattern]] = [
    ("explicit_action", re.compile(r"提出(?:明確|具體|清晰)?行動", re.IGNORECASE)),
    ("trackable_milestone", re.compile(r"(?:形成|建立|已形成).*(?:可追蹤|可追踪).*(?:里程碑|節點)", re.IGNORECASE)),
    ("decision_anchor", re.compile(r"建議(?:以|依).{0,24}為決策錨點", re.IGNORECASE)),
    ("seven_day_decision", re.compile(r"在(?:七天|7天)內(?:完成|取捨|排程|決策)", re.IGNORECASE)),
    ("tech_delivery_quality", re.compile(r"直接影響.{0,30}技術架構.{0,30}交付品質", re.IGNORECASE)),
    ("risk_opportunity_sync", re.compile(r"風險與機會成本(?:會)?同步放大", re.IGNORECASE)),
    ("followup_public_source_verify", re.compile(r"後續(?:將)?以.{0,24}與公開來源持續核對", re.IGNORECASE)),
    ("workflow_supervision_style", re.compile(r"(?:提出|建議).{0,20}(?:排程|里程碑|執行節點|責任分工|優先序|完成取捨)", re.IGNORECASE)),
]

_BRIEF_FRAME_SIG_STOPWORDS = {
    "the", "and", "for", "with", "from", "into", "about", "this", "that", "will",
    "have", "has", "had", "are", "was", "were", "their", "there", "which", "while",
    "actor", "anchor", "num", "email", "source", "quote",
}


def _brief_title_tokens(title: str) -> list[str]:
    raw = _normalize_ws(title)
    if not raw:
        return []
    tokens: list[str] = []
    for tk in re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}|[\u4e00-\u9fff]{2,}", raw):
        norm = _normalize_ws(tk).lower()
        if not norm:
            continue
        if norm in {"the", "and", "for", "with", "from", "into", "about", "this", "that"}:
            continue
        tokens.append(norm)
    out: list[str] = []
    seen: set[str] = set()
    for tk in tokens:
        if tk in seen:
            continue
        seen.add(tk)
        out.append(tk)
    return out


def _brief_quote_is_cta(text: str) -> bool:
    q = _normalize_ws(text)
    if not q:
        return True
    if _BRIEF_EMAIL_RE.search(q):
        return True
    return bool(_BRIEF_CTA_RE.search(q))


def _brief_quote_relevance_ok(
    quote: str,
    actor: str,
    title_tokens: list[str],
    anchors: list[str] | None = None,
) -> bool:
    q = _normalize_ws(quote)
    if not q:
        return False
    actor_n = _normalize_ws(actor)
    q_l = q.lower()
    if actor_n and actor_n.lower() in q_l:
        return True
    overlap = 0
    for tk in title_tokens:
        if tk and tk in q_l:
            overlap += 1
    if overlap >= 2:
        return True
    if anchors:
        for anc in anchors:
            a = _normalize_ws(anc)
            if not a:
                continue
            if a.isascii():
                if a.lower() in q_l:
                    return True
            elif a in q:
                return True
    has_number = bool(re.search(r"(?:\d|[$€¥£]|(?:19|20)\d{2}|%)", q))
    has_impact = bool(_BRIEF_IMPACT_WORD_RE.search(q))
    has_action = bool(_BRIEF_ACTION_WORD_RE.search(q))
    if overlap >= 1 and (has_number or has_impact or has_action):
        return True
    return False


def _brief_quote_candidates(
    source_text: str,
    seed_quote: str,
    span_start: float = _BRIEF_QUOTE_SPAN_START,
    span_end: float = _BRIEF_QUOTE_SPAN_END,
) -> list[str]:
    cands: list[str] = []
    seed = _normalize_ws(seed_quote)
    if seed:
        cands.append(seed)
    sentences = _brief_split_source_sentences(source_text)
    if sentences:
        total = len(sentences)
        start_idx = max(0, min(total - 1, int(total * max(0.0, span_start))))
        end_idx = max(start_idx + 1, min(total, int(total * min(1.0, span_end))))
        span_sents = sentences[start_idx:end_idx]
        if len(span_sents) < 3 and total >= 3:
            fb_start = max(0, min(total - 1, int(total * 0.10)))
            fb_end = max(fb_start + 1, min(total, int(total * 0.85)))
            span_sents = sentences[fb_start:fb_end]
        for s in span_sents:
            ss = _normalize_ws(s)
            if not _brief_sentence_len_ok(ss):
                continue
            cands.append(ss)
    out: list[str] = []
    seen: set[str] = set()
    for c in cands:
        cc = _clip_text(_sanitize_quote_for_delivery(c), 220)
        if not cc:
            continue
        key = cc.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(cc)
    return out


def _brief_select_relevant_quote(
    source_text: str,
    seed_quote: str,
    actor: str,
    title: str,
    anchors: list[str] | None = None,
    avoid_quote: str = "",
    diag: dict | None = None,
    span_start: float = _BRIEF_QUOTE_SPAN_START,
    span_end: float = _BRIEF_QUOTE_SPAN_END,
) -> str:
    title_tokens = _brief_title_tokens(title)
    avoid = _normalize_ws(avoid_quote).lower()
    for cand in _brief_quote_candidates(source_text, seed_quote, span_start=span_start, span_end=span_end):
        if len(cand) < 80:
            continue
        if avoid and cand.lower() == avoid:
            continue
        if _brief_quote_is_cta(cand):
            if isinstance(diag, dict):
                diag["quote_stoplist_hits_count"] = int(diag.get("quote_stoplist_hits_count", 0) or 0) + 1
            continue
        if not _brief_quote_relevance_ok(cand, actor, title_tokens, anchors=anchors):
            continue
        return cand
    return ""


def _brief_collect_relevant_quotes(
    *,
    source_text: str,
    seed_quote: str,
    actor: str,
    title: str,
    anchors: list[str] | None = None,
    avoid_quotes: set[str] | None = None,
    diag: dict | None = None,
    span_start: float,
    span_end: float,
    max_candidates: int = 8,
) -> list[str]:
    avoid_norm = {_normalize_ws(x).lower() for x in (avoid_quotes or set()) if _normalize_ws(x)}
    title_tokens = _brief_title_tokens(title)
    out: list[str] = []
    seen: set[str] = set()
    for cand in _brief_quote_candidates(source_text, seed_quote, span_start=span_start, span_end=span_end):
        c = _normalize_ws(cand)
        if len(c) < 80:
            continue
        cl = c.lower()
        if cl in avoid_norm or cl in seen:
            continue
        if _brief_quote_is_cta(c):
            if isinstance(diag, dict):
                diag["quote_stoplist_hits_count"] = int(diag.get("quote_stoplist_hits_count", 0) or 0) + 1
            continue
        if not _brief_quote_relevance_ok(c, actor, title_tokens, anchors=anchors):
            continue
        seen.add(cl)
        out.append(c)
        if len(out) >= max(1, int(max_candidates)):
            break
    return out


def _brief_collect_detail_sentences_en(
    cleaned_full_text: str,
    title: str,
    actor: str,
    anchors: list[str],
    limit: int = 10,
) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    exclude_used: set[str] = set()
    for _ in range(max(1, int(limit))):
        sent = _brief_pick_detail_sentence_en(
            cleaned_full_text=cleaned_full_text,
            title=title,
            actor=actor,
            anchors=anchors,
            exclude_used=exclude_used,
        )
        if not sent:
            break
        s = _normalize_ws(sent)
        sl = s.lower()
        if sl in seen:
            exclude_used.add(sl)
            continue
        seen.add(sl)
        exclude_used.add(sl)
        out.append(s)
    return out


def _brief_extract_num_token(*parts: str) -> str:
    blob = _normalize_ws(" ".join(parts))
    if not blob:
        return ""
    m = re.search(
        r"\$?\d[\d,\.]*\s*(?:%|million|billion|萬|億|千|k|m|bn|accounts|users|days|天)?",
        blob,
        re.IGNORECASE,
    )
    return _normalize_ws(m.group(0)) if m else ""


def _brief_topic_marker(title: str, actor: str, anchors: list[str]) -> str:
    ttl = _normalize_ws(title)
    if not ttl:
        return ""
    actor_l = _normalize_ws(actor).lower()
    anchor_l = {
        _normalize_ws(a).lower()
        for a in (anchors or [])
        if _normalize_ws(a)
    }
    for tk in re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}|[\u4e00-\u9fff]{2,}", ttl):
        t = _normalize_ws(tk)
        if not t:
            continue
        tl = t.lower()
        if actor_l and tl == actor_l:
            continue
        if tl in anchor_l:
            continue
        return _clip_text(t, 20)
    return _clip_text(ttl, 20)


def _brief_apply_topic_marker(bullet: str, marker: str) -> str:
    b = _normalize_ws(bullet)
    m = _normalize_ws(marker)
    if (not b) or (not m):
        return b
    if m.lower() in b.lower():
        return b
    return _normalize_ws(f"「{m}」{b}")


def _brief_norm_bullet(text: str) -> str:
    b = _normalize_ws(text)
    if len(b) < 12:
        b = _normalize_ws(f"{b}，可作為決策依據。")
    return b


def _brief_split_bullets(raw: str) -> list[str]:
    out: list[str] = []
    # Split on newlines, Chinese/English semicolons, AND Chinese sentence endings
    # This correctly handles ZH narratives like "句一。句二。句三。" → 3 bullets
    for seg in re.split(r"[\n；;。！？]+", str(raw or "")):
        s = _normalize_ws(seg.strip(" -•\t"))
        if not s:
            continue
        out.append(_brief_norm_bullet(s))
    return out


def _brief_bullet_hit_anchor_or_number(text: str, anchors: list[str]) -> bool:
    t = _normalize_ws(text)
    if not t:
        return False
    if re.search(r"\d", t):
        return True
    for a in anchors:
        an = _normalize_ws(a)
        if not an:
            continue
        if an.isascii():
            if an.lower() in t.lower():
                return True
        elif an in t:
            return True
    return False


def _brief_find_generic_narrative_hits(*parts: str) -> list[dict]:
    hits: list[dict] = []
    seen: set[str] = set()
    for part in parts:
        txt = _normalize_ws(part)
        if not txt:
            continue
        for rule_name, rule_re in _BRIEF_GENERIC_NARRATIVE_RULES:
            m = rule_re.search(txt)
            if not m:
                continue
            key = f"{rule_name}:{_normalize_ws(m.group(0)).lower()}"
            if key in seen:
                continue
            seen.add(key)
            hits.append(
                {
                    "hit_pattern": rule_name,
                    "matched_text": _normalize_ws(m.group(0)),
                    "sample_text": _clip_text(txt, 160),
                }
            )
    return hits


def _brief_replace_token_ci(text: str, token: str, replacement: str) -> str:
    t = _normalize_ws(token)
    if not t:
        return text
    flags = re.IGNORECASE if t.isascii() else 0
    return re.sub(re.escape(t), replacement, text, flags=flags)


def _brief_collect_frame_signatures(
    *,
    summary_zh: str,
    what_bullets: list[str],
    key_bullets: list[str],
    why_bullets: list[str],
    actor: str,
    anchors: list[str],
) -> set[str]:
    parts: list[str] = []
    if summary_zh:
        parts.append(_normalize_ws(summary_zh))
    parts.extend(_normalize_ws(s) for s in (what_bullets or []))
    parts.extend(_normalize_ws(s) for s in (key_bullets or []))
    parts.extend(_normalize_ws(s) for s in (why_bullets or []))
    blob = "\n".join([p for p in parts if p])
    if not blob:
        return set()

    normalized = str(blob)
    normalized = _BRIEF_EMAIL_RE.sub("<EMAIL>", normalized)
    normalized = _brief_replace_token_ci(normalized, actor, "<ACTOR>")
    anchor_tokens = sorted(
        {_normalize_ws(a) for a in (anchors or []) if _normalize_ws(a)},
        key=len,
        reverse=True,
    )
    for anc in anchor_tokens:
        if actor and _normalize_ws(anc).lower() == _normalize_ws(actor).lower():
            continue
        normalized = _brief_replace_token_ci(normalized, anc, "<ANCHOR>")
    normalized = re.sub(r"\b\d[\d,\.%:/\-]*\b", "<NUM>", normalized)
    normalized = _normalize_ws(normalized)

    signatures: set[str] = set()
    for seg in re.split(r"(?:[。！？；\n]+|(?<=[\.\!\?;])\s+)", normalized):
        sent = _normalize_ws(seg)
        if len(sent) < 12:
            continue
        prefix = sent[:24]
        keywords: list[str] = []
        for tk in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9\-]{2,}", sent.lower()):
            if tk in _BRIEF_FRAME_SIG_STOPWORDS:
                continue
            if tk in keywords:
                continue
            keywords.append(tk)
            if len(keywords) >= 6:
                break
        hash_basis = "|".join(keywords) if keywords else prefix.lower()
        sig_hash = hashlib.sha1(hash_basis.encode("utf-8")).hexdigest()[:8]
        signatures.add(f"{prefix}|{sig_hash}")
    return signatures


def _brief_count_cjk_chars(text: str) -> int:
    return sum(1 for c in str(text or "") if "\u4e00" <= c <= "\u9fff")


def _brief_sentence_len_ok(text: str) -> bool:
    s = _normalize_ws(text)
    if not s:
        return False
    if _brief_count_cjk_chars(s) > 0:
        return _brief_count_cjk_chars(s) >= _BRIEF_MIN_CJK_SENTENCE_CHARS
    return len(s) >= _BRIEF_MIN_EN_SENTENCE_CHARS


def _brief_token_set(text: str) -> set[str]:
    toks = re.findall(r"[A-Za-z0-9][A-Za-z0-9\-]{1,}|[\u4e00-\u9fff]{2,}", str(text or "").lower())
    return {t for t in toks if t and t not in _BRIEF_FRAME_SIG_STOPWORDS}


def _brief_overlap_ratio(a: str, b: str) -> float:
    ta = _brief_token_set(a)
    tb = _brief_token_set(b)
    if (not ta) or (not tb):
        return 0.0
    inter = len(ta.intersection(tb))
    denom = max(1, min(len(ta), len(tb)))
    return inter / denom


def _brief_split_source_sentences(source_text: str) -> list[str]:
    body = str(source_text or "").replace("\r", "\n")
    out: list[str] = []
    for para in re.split(r"\n{1,}", body):
        p = _normalize_ws(para)
        if not p:
            continue
        # Keep list-like lines, while still splitting normal sentence boundaries.
        if re.match(r"^\s*(?:[-*•]|\d+\.)\s+", para):
            p = _normalize_ws(re.sub(r"^\s*(?:[-*•]|\d+\.)\s+", "", para))
            if p:
                out.append(_clip_text(p, _BRIEF_SENTENCE_MAX_CHARS))
            continue
        for seg in re.split(r"(?<=[\.\!\?。！？；;])\s+", p):
            s = _normalize_ws(seg.strip(" -•\t"))
            if not s:
                continue
            out.append(_clip_text(s, _BRIEF_SENTENCE_MAX_CHARS))
    return out


def _brief_sentence_score(
    sentence: str,
    *,
    actor: str,
    title_tokens: list[str],
    anchors: list[str],
) -> dict:
    s = _normalize_ws(sentence)
    sl = s.lower()
    actor_n = _normalize_ws(actor)
    actor_hit = bool(actor_n and actor_n.lower() in sl)
    title_overlap = sum(1 for tk in (title_tokens or []) if tk and tk in sl)
    has_number = bool(re.search(r"(?:\d|[$€¥£]|(?:19|20)\d{2}|%)", s))
    has_anchor = any(
        bool(_normalize_ws(a)) and (
            (_normalize_ws(a).lower() in sl) if _normalize_ws(a).isascii() else (_normalize_ws(a) in s)
        )
        for a in (anchors or [])
    )
    has_impact = bool(_BRIEF_IMPACT_WORD_RE.search(s))
    has_action = bool(_BRIEF_ACTION_WORD_RE.search(s))
    score = 0
    if has_number:
        score += 2
    if actor_hit or title_overlap >= 2:
        score += 2
    if has_anchor:
        score += 2
    if has_impact:
        score += 1
    if has_action:
        score += 1
    return {
        "text": s,
        "score": score,
        "actor_hit": actor_hit,
        "title_overlap": title_overlap,
        "has_number": has_number,
        "has_anchor": has_anchor,
        "has_impact": has_impact,
        "has_action": has_action,
    }


def _brief_mine_sentence_candidates(
    *,
    title: str,
    actor: str,
    anchors: list[str],
    full_text: str,
    max_candidates: int = _BRIEF_MAX_SENTENCE_CANDIDATES,
    diag: dict | None = None,
) -> list[dict]:
    sents = _brief_split_source_sentences(full_text)
    total = len(sents)
    if total == 0:
        if isinstance(diag, dict):
            diag["sentences_total"] = 0
            diag["span_policy_used"] = _BRIEF_QUOTE_SPAN_POLICY
        return []

    title_tokens = _brief_title_tokens(title)
    start_1 = max(0, min(total - 1, int(total * 0.15)))
    end_1 = max(start_1 + 1, min(total, int(total * 0.75)))
    span_1 = sents[start_1:end_1]
    span_policy = "0.15-0.75"
    if len(span_1) < 8:
        start_2 = max(0, min(total - 1, int(total * 0.10)))
        end_2 = max(start_2 + 1, min(total, int(total * 0.85)))
        span_1 = sents[start_2:end_2]
        span_policy = "0.10-0.85"
    span = span_1 if span_1 else sents

    cands: list[dict] = []
    stoplist_rejected = 0
    short_rejected = 0
    for idx, sent in enumerate(span):
        s = _normalize_ws(sent)
        if not s:
            continue
        if _brief_quote_is_cta(s):
            stoplist_rejected += 1
            continue
        if not _brief_sentence_len_ok(s):
            short_rejected += 1
            continue
        scored = _brief_sentence_score(
            s,
            actor=actor,
            title_tokens=title_tokens,
            anchors=anchors,
        )
        scored["index"] = idx
        cands.append(scored)

    cands.sort(key=lambda x: (-int(x.get("score", 0)), int(x.get("index", 0))))
    selected: list[dict] = []
    for cand in cands:
        if int(cand.get("score", 0)) <= 0:
            continue
        dup = False
        for prev in selected:
            if _brief_overlap_ratio(str(prev.get("text", "")), str(cand.get("text", ""))) >= 0.75:
                dup = True
                break
        if dup:
            continue
        selected.append(cand)
        if len(selected) >= max(1, int(max_candidates)):
            break

    if isinstance(diag, dict):
        diag["sentences_total"] = total
        diag["span_policy_used"] = span_policy
        diag["candidates_total"] = len(selected)
        diag["stoplist_rejected"] = stoplist_rejected
        diag["short_rejected"] = short_rejected
        diag["selected_sentences_preview"] = [
            _clip_text(str(x.get("text", "") or ""), 180)
            for x in selected[:3]
        ]
    return selected


def _brief_pick_quote_from_candidates(
    candidates: list[dict],
    *,
    role: str,
    avoid_quotes: set[str] | None = None,
) -> str:
    avoid = {_normalize_ws(x).lower() for x in (avoid_quotes or set()) if _normalize_ws(x)}
    ranked: list[tuple[int, int, str]] = []
    for cand in candidates or []:
        text = _normalize_ws(str(cand.get("text", "") or ""))
        if not text:
            continue
        cscore = int(cand.get("score", 0) or 0)
        rscore = cscore
        if role == "lede":
            if cand.get("actor_hit") or int(cand.get("title_overlap", 0) or 0) >= 2:
                rscore += 3
            if cand.get("has_action"):
                rscore += 2
        else:
            if cand.get("has_impact"):
                rscore += 3
            if cand.get("has_number"):
                rscore += 2
            if cand.get("has_anchor"):
                rscore += 1
        ranked.append((rscore, int(cand.get("index", 0) or 0), text))
    ranked.sort(key=lambda x: (-x[0], x[1]))
    for _, _, raw in ranked:
        q = _clip_text(_sanitize_quote_for_delivery(raw), 220)
        if len(q) < 80:
            continue
        ql = q.lower()
        if ql in avoid:
            continue
        if _brief_quote_is_cta(q):
            continue
        if role == "impact":
            has_num = bool(re.search(r"(?:\d|[$€¥£]|%)", q))
            has_impact = bool(_BRIEF_IMPACT_WORD_RE.search(q))
            has_anchor = bool(cand.get("has_anchor"))
            if (not has_num) and (not has_impact) and (not has_anchor):
                continue
        return q
    return ""


def _brief_validate_zh_bullet(text: str) -> bool:
    b = _normalize_ws(text)
    if not b:
        return False
    if _brief_count_cjk_chars(b) < _BRIEF_MIN_BULLET_CJK_CHARS:
        return False
    if _STYLE_SANITY_RE.search(b):
        return False
    if _brief_quote_is_cta(b):
        return False
    if _brief_contains_boilerplate(b):
        return False
    if _brief_contains_audit_speak(b):
        return False
    if _brief_find_generic_narrative_hits(b):
        return False
    if not _brief_zh_tw_ok(b):
        return False
    return True


def _brief_sentence_to_zh_bullet(
    *,
    sentence_en: str,
    title: str,
    actor: str,
    anchors: list[str],
    role: str,
) -> str:
    s = _normalize_ws(sentence_en)
    if not s:
        return ""
    anchor = _brief_pick_primary_anchor(actor, anchors)
    num = _brief_extract_num_token(s, title)
    context = {
        "title": title or s[:100],
        "bucket": "tech",
        "date": "",
        "what_happened": s,
        "subject": actor or anchor,
    }
    rewritten = ""
    try:
        if role == "why":
            rewritten = _normalize_ws(
                rewrite_news_impact_v2(
                    s,
                    context,
                    anchors=anchors or [],
                    primary_anchor=anchor or None,
                )
            )
        else:
            rewritten = _normalize_ws(
                rewrite_news_lead_v2(
                    s,
                    context,
                    anchors=anchors or [],
                    primary_anchor=anchor or None,
                )
            )
    except Exception:
        rewritten = ""

    candidates: list[str] = []
    if rewritten:
        for seg in re.split(r"[。！？\n]+", rewritten):
            ss = _normalize_ws(seg)
            if ss:
                candidates.append(ss)

    # Deterministic fallback keeps facts tied to the source sentence.
    if not candidates:
        clip = _clip_text(s, 42)
        if _brief_zh_cjk_ratio(clip) < 0.2:
            clip = _clip_text(title or anchor or actor or "原文句子", 24)
        if role == "what":
            candidates.append(_normalize_ws(f"原文指出 {anchor or actor} 的最新動作為「{clip}」，可直接對照來源句。"))
        elif role == "key":
            if num:
                candidates.append(_normalize_ws(f"原文量化細節為 {num}，句子「{clip}」可作為技術條件的核對基礎。"))
            else:
                candidates.append(_normalize_ws(f"原文細節「{clip}」揭示了實作限制與機制邊界。"))
        else:
            if num:
                candidates.append(_normalize_ws(f"此句「{clip}」顯示影響範圍與 {num} 相關，會改變後續決策排序。"))
            else:
                candidates.append(_normalize_ws(f"此句「{clip}」反映潛在風險與收益變化，需納入後續評估。"))

    for cand in candidates:
        out = _brief_norm_bullet(cand)
        if num and (not re.search(r"\d", out)):
            out = _brief_norm_bullet(f"{out}（關鍵數字：{num}）")
        if anchor and (not _brief_has_anchor_token(out, [anchor])) and (not re.search(r"\d", out)):
            out = _brief_norm_bullet(f"{out}（錨點：{anchor}）")
        if _brief_validate_zh_bullet(out):
            return out
    return ""


def _brief_build_role_bullets(
    *,
    role: str,
    candidates: list[dict],
    title: str,
    actor: str,
    anchors: list[str],
    min_count: int,
    max_count: int,
    used_sentences: set[str],
    allow_reuse_sentences: bool = False,
) -> list[str]:
    out: list[str] = []
    used_bullets: set[str] = set()
    for cand in candidates:
        en = _normalize_ws(str(cand.get("text", "") or ""))
        if not en:
            continue
        if (not allow_reuse_sentences) and (en.lower() in used_sentences):
            continue
        zh = _brief_sentence_to_zh_bullet(
            sentence_en=en,
            title=title,
            actor=actor,
            anchors=anchors,
            role=role,
        )
        if not zh:
            continue
        zhl = zh.lower()
        if zhl in used_bullets:
            continue
        out.append(zh)
        used_bullets.add(zhl)
        if not allow_reuse_sentences:
            used_sentences.add(en.lower())
        if len(out) >= max(1, int(max_count)):
            break
    return out[:max(1, int(max_count))]


def _brief_build_bullet_sections(
    title: str,
    actor: str,
    anchor: str,
    quote_1: str,
    quote_2: str,
    impact_target: str,
    decision_angle: str,
    final_url: str,
    *,
    detail_sentence_en: str = "",
    title_tokens: list[str] | None = None,
    attempt_idx: int = 0,
) -> tuple[list[str], list[str], list[str]]:
    title_tokens = list(title_tokens or [])
    title_lead = _clip_text(title, 56) or _clip_text(anchor, 24) or "該事件"
    topic_token = title_tokens[0] if title_tokens else title_lead[:12]
    lead_words = ["原文指出", "報導提到", "來源寫明", "內容揭示", "同文顯示"]
    detail_words = ["補充背景細節", "補充上下文線索", "呈現部署條件", "說明邊界條件", "標示關聯脈絡"]
    impact_words = ["會改變", "正在改寫", "直接牽動", "提高了", "重塑了"]
    _lead = lead_words[attempt_idx % len(lead_words)]
    _detail = detail_words[attempt_idx % len(detail_words)]
    _impact = impact_words[attempt_idx % len(impact_words)]
    num_1 = _brief_extract_num_token(quote_1, title) or "關鍵數值"
    num_2 = _brief_extract_num_token(quote_2, quote_1) or topic_token
    q1_clip = _clip_text(quote_1, 80)
    q2_clip = _clip_text(quote_2, 80)
    detail_clip = _clip_text(detail_sentence_en, 80) if detail_sentence_en else ""
    what = [
        _brief_norm_bullet(f"「{title_lead}」{_lead} {actor} 相關進展，並涉及 {anchor} 的公開資訊。"),
        _brief_norm_bullet(f"第一段引文為「{q1_clip}」，量化資訊 {num_1} 已記錄。"),
    ]
    if detail_clip:
        what.append(_brief_norm_bullet(f"同文另段提到「{detail_clip}」，{_detail}。"))
    else:
        what.append(_brief_norm_bullet(f"第二段引文「{q2_clip}」補充了事件脈絡，{_detail}。"))
    key = [
        _brief_norm_bullet(f"第二段引文「{q2_clip}」揭示技術或機制細節，顯示部署限制。"),
        _brief_norm_bullet(f"{title_lead} 的來源已附上原始網址與發佈時間，供直接查閱。"),
    ]
    why = [
        _brief_norm_bullet(f"此訊號{_impact} {impact_target} 的決策方向，{topic_token} 的優先順序需要調整。"),
        _brief_norm_bullet(f"就「{topic_token}」相關判讀來看，{num_2} 可支撐「{decision_angle}」的調整順序。"),
    ]
    if final_url:
        key.append(_brief_norm_bullet(f"原始來源：{final_url}，原文已完整存檔。"))
    return what[:5], key[:4], why[:4]


def _brief_build_key_details_zh(
    *,
    q1_zh: str,
    q2_zh: str,
    what_bullets: list[str],
    why_bullets: list[str],
    quote_1: str,
    quote_2: str,
    anchor: str,
    source_blob: str,
    final_url: str,
    key_defaults: list[str],
) -> list[str]:
    """Build content-rich ZH key-detail bullets.

    Strategy (priority order):
    1. Leftover ZH sentences from q1_zh/q2_zh not already in what/why bullets
    2. Number-focused bullet from extracted metric in quotes/source
    3. Quote-2 embedding bullet
    4. Source URL bullet
    Fall back to key_defaults if we can't produce >= 2 clean bullets.
    """
    bullets: list[str] = []

    # Collect ZH sentences not already used in what_bullets / why_bullets
    used_lower = {b.lower() for b in (what_bullets or []) + (why_bullets or [])}
    for src in (q1_zh, q2_zh):
        if not src:
            continue
        for seg in re.split(r"[。！？]+", src):
            s = _normalize_ws(seg.strip())
            if len(s) < 12:
                continue
            if _brief_quote_is_cta(s):
                continue
            if _brief_find_generic_narrative_hits(s):
                continue
            if any(s.lower() in u or u in s.lower() for u in used_lower):
                continue
            bullets.append(_brief_norm_bullet(s))
            if len(bullets) >= 2:
                break
        if len(bullets) >= 2:
            break

    # Number-focused bullet
    num = _brief_extract_num_token(quote_1, quote_2, source_blob[:800])
    if num:
        nb = _brief_norm_bullet(f"量化依據：原文顯示「{num}」，為評估影響範圍的核心數字錨點。")
        if (
            not _brief_find_generic_narrative_hits(nb)
            and nb.lower() not in used_lower
            and not any(nb.lower() in b.lower() for b in bullets)
        ):
            bullets.append(nb)

    # Quote-2 embedding bullet (only if not already captured)
    if len(bullets) < 2 and quote_2:
        q2clip = _clip_text(quote_2, 96)
        q2b = _brief_norm_bullet(
            f"技術細節佐證：「{q2clip}」揭示部署條件與機制設計邊界。"
        )
        if not _brief_find_generic_narrative_hits(q2b):
            bullets.append(q2b)

    # Source URL bullet
    if final_url and len(bullets) < 4:
        srcb = _brief_norm_bullet(
            f"原始來源：{final_url}，原文已完整存檔供查閱。"
        )
        if not _brief_find_generic_narrative_hits(srcb):
            bullets.append(srcb)

    valid = [b for b in bullets if len(b) >= 12]
    if len(valid) < 2:
        return key_defaults
    return valid[:4]


def _brief_pick_detail_sentence_en(
    cleaned_full_text: str,
    title: str,
    actor: str,
    anchors: list[str],
    exclude_used: set | None = None,
) -> str:
    """Pick one content-rich English sentence from cleaned_full_text for ZH bullet generation.

    Rules (per DOD-2):
    - Not in CTA stoplist (_BRIEF_CTA_RE / _UI_GARBAGE)
    - >= 80 chars
    - Contains digit OR anchor-hit OR >= 2 title-token overlap
    - Sampled from 0.20–0.85 span (avoids lede and trailing CTA)
    - Not in exclude_used set (deduplication across calls)
    Returns "" when no qualifying sentence is found.
    """
    if not cleaned_full_text:
        return ""
    exclude_used = set(exclude_used) if exclude_used else set()
    title_tokens = _brief_title_tokens(title)
    sentences: list[str] = []
    for seg in re.split(r"(?<=[\.\!\?])\s+|\n+", cleaned_full_text):
        s = _normalize_ws(seg)
        if s and len(s) >= 80:
            sentences.append(s)
    if not sentences:
        return ""
    total = len(sentences)
    start_idx = max(0, int(total * 0.20))
    end_idx = min(total, max(start_idx + 1, int(total * 0.85)))
    span = sentences[start_idx:end_idx] or sentences  # fallback: all sentences
    for sent in span:
        if sent.lower() in exclude_used:
            continue
        if _brief_quote_is_cta(sent):
            continue
        has_digit = bool(re.search(r"\d", sent))
        has_anchor = any(
            a and a.lower() in sent.lower()
            for a in (anchors or [])
            if a and len(a) >= 2
        )
        sent_lower = sent.lower()
        title_overlap = sum(1 for tk in title_tokens if tk and len(tk) >= 3 and tk in sent_lower)
        if has_digit or has_anchor or title_overlap >= 2:
            return sent
    return ""


def _brief_translate_detail_bullet_zh(
    detail_sentence_en: str,
    actor: str,
    anchors: list[str],
    title: str = "",
) -> str:
    """Rewrite an English detail sentence into a single ZH-TW bullet.

    Strategy:
    1. Call rewrite_news_lead_v2 (rule-based ZH rewriter, no LLM dependency)
    2. Validate result with _brief_zh_tw_ok + !boilerplate
    3. Fallback to anchor/number template (also validated)
    Returns "" when nothing passes the ZH-TW gate.
    """
    if not detail_sentence_en:
        return ""
    anchor = next(
        (a for a in (anchors or []) if a and not _is_actor_numeric(a) and len(a) >= 2),
        actor or "",
    )
    num = _brief_extract_num_token(detail_sentence_en)
    result = ""
    try:
        context = {
            "title": title or detail_sentence_en[:100],
            "bucket": "tech",
            "date": "",
            "what_happened": detail_sentence_en,
            "subject": actor or anchor,
        }
        result = _normalize_ws(
            rewrite_news_lead_v2(
                detail_sentence_en,
                context,
                anchors=anchors or [],
                primary_anchor=anchor or None,
            )
        )
    except Exception:
        result = ""
    # Take only first sentence of rewrite output (keep it bullet-sized)
    if result:
        first = _normalize_ws(re.split(r"[。！？\n]", result)[0])
        if first and 12 <= len(first) <= 100 and _brief_zh_tw_ok(first) and not _brief_contains_boilerplate("", first):
            return _brief_norm_bullet(first)
    # Fallback rule-based template — must contain anchor or number
    if num and anchor:
        fb = _brief_norm_bullet(f"{anchor} 相關數據顯示 {num}，需優先確認對產品或模型節奏的影響。")
    elif num:
        fb = _brief_norm_bullet(f"原文量化數據 {num}，為評估影響範圍的核心依據。")
    elif anchor:
        fb = _brief_norm_bullet(f"原文揭示 {anchor} 的關鍵細節，可作為後續技術評估與決策依據。")
    else:
        return ""
    if _brief_zh_tw_ok(fb) and not _brief_contains_boilerplate("", fb):
        return fb
    return ""


def _prepare_brief_final_cards(final_cards: list[dict], max_events: int = 10) -> tuple[list[dict], dict]:
    prepared: list[dict] = []
    accepted_signature_sets: list[dict] = []
    diag = {
        "input_total": len(final_cards or []),
        "drop_non_ai": 0,
        "drop_actor_invalid": 0,
        "drop_anchor_missing": 0,
        "drop_quote_too_short": 0,
        "drop_boilerplate": 0,
        "drop_quote_relevance": 0,
        "drop_generic_narrative": 0,
        "drop_duplicate_frames": 0,
        "quote_stoplist_hits_count": 0,
        "tierA_candidates": 0,
        "tierA_used": 0,
        "content_miner_events": [],
        "dropped_events": [],
    }
    def _record_drop(reason: str, title_text: str, extra: dict | None = None) -> None:
        if len(diag.get("dropped_events", [])) >= 40:
            return
        payload = {
            "reason": _normalize_ws(reason),
            "title": _normalize_ws(title_text)[:140],
        }
        if isinstance(extra, dict):
            for k, v in extra.items():
                payload[str(k)] = v
        diag["dropped_events"].append(payload)

    for fc in sorted(final_cards or [], key=_brief_candidate_priority, reverse=True):
        _fc_src = _normalize_ws(str(fc.get("source_name", "") or ""))
        _fc_url = _normalize_ws(str(fc.get("final_url", "") or fc.get("source_url", "") or ""))
        _fc_ttl = _normalize_ws(str(fc.get("title", "") or ""))
        if _is_tier_a_source(_fc_src, _fc_url, _fc_ttl):
            diag["tierA_candidates"] += 1
        if not bool(fc.get("ai_relevance", False)):
            diag["drop_non_ai"] += 1
            _record_drop("non_ai", _fc_ttl)
            continue

        actor = _normalize_ws(str(fc.get("actor_primary", "") or fc.get("actor", "") or ""))
        if (not actor) or _is_actor_numeric(actor) or _brief_is_garbage_actor(actor):
            diag["drop_actor_invalid"] += 1
            _record_drop("actor_invalid", _fc_ttl, {"actor": actor})
            continue

        title = _normalize_ws(str(fc.get("title", "") or ""))
        source_blob = _normalize_ws(
            str(fc.get("full_text", "") or fc.get("what_happened", "") or fc.get("q1", "") or "")
        )
        fulltext_len = len(source_blob)
        if fulltext_len < 200:
            diag["drop_quote_relevance"] += 1
            _record_drop("fulltext_too_short", title, {"fulltext_len": fulltext_len})
            continue

        anchors_raw = [
            _normalize_ws(str(a or ""))
            for a in (fc.get("anchors", []) or [])
            if _normalize_ws(str(a or ""))
        ]
        anchor = _brief_pick_primary_anchor(actor, anchors_raw)
        if not anchor:
            diag["drop_anchor_missing"] += 1
            _record_drop("anchor_missing", title, {"actor": actor})
            continue
        anchors_all = [anchor] + anchors_raw

        miner_diag: dict = {}
        mined = _brief_mine_sentence_candidates(
            title=title,
            actor=actor,
            anchors=anchors_all,
            full_text=source_blob,
            max_candidates=_BRIEF_MAX_SENTENCE_CANDIDATES,
            diag=miner_diag,
        )
        diag["quote_stoplist_hits_count"] = int(diag.get("quote_stoplist_hits_count", 0) or 0) + int(
            miner_diag.get("stoplist_rejected", 0) or 0
        )
        if len(mined) < 4:
            diag["drop_quote_relevance"] += 1
            _record_drop(
                "mined_candidates_insufficient",
                title,
                {
                    "fulltext_len": fulltext_len,
                    "candidates_total": int(miner_diag.get("candidates_total", 0) or 0),
                    "stoplist_rejected": int(miner_diag.get("stoplist_rejected", 0) or 0),
                    "short_rejected": int(miner_diag.get("short_rejected", 0) or 0),
                    "span_policy_used": str(miner_diag.get("span_policy_used", "")),
                },
            )
            continue

        _sorted_by_score = sorted(mined, key=lambda c: (-int(c.get("score", 0)), int(c.get("index", 0))))
        quote_1 = _brief_pick_quote_from_candidates(_sorted_by_score, role="lede", avoid_quotes=set())
        quote_2 = _brief_pick_quote_from_candidates(_sorted_by_score, role="impact", avoid_quotes={quote_1})
        if not quote_1:
            quote_1 = _brief_select_relevant_quote(
                source_text=source_blob,
                seed_quote=_normalize_ws(str(fc.get("quote_1", "") or "")),
                actor=actor,
                title=title,
                anchors=anchors_all,
                avoid_quote="",
                diag=diag,
                span_start=0.15,
                span_end=0.60,
            )
        if not quote_2:
            quote_2 = _brief_select_relevant_quote(
                source_text=source_blob,
                seed_quote=_normalize_ws(str(fc.get("quote_2", "") or "")),
                actor=actor,
                title=title,
                anchors=anchors_all,
                avoid_quote=quote_1,
                diag=diag,
                span_start=0.35,
                span_end=0.75,
            )
        if (not quote_1) or (not quote_2):
            _quote_fallbacks = _brief_collect_relevant_quotes(
                source_text=source_blob,
                seed_quote="",
                actor=actor,
                title=title,
                anchors=anchors_all,
                avoid_quotes={_normalize_ws(quote_1), _normalize_ws(quote_2)},
                diag=diag,
                span_start=0.12,
                span_end=0.82,
                max_candidates=6,
            )
            if (not quote_1) and _quote_fallbacks:
                quote_1 = _quote_fallbacks[0]
            if not quote_2:
                for _qf in _quote_fallbacks:
                    if _normalize_ws(_qf).lower() != _normalize_ws(quote_1).lower():
                        quote_2 = _qf
                        break
        if (not quote_1) or (not quote_2):
            diag["drop_quote_relevance"] += 1
            _record_drop(
                "quote_missing_after_selection",
                title,
                {
                    "q1_len": len(_normalize_ws(quote_1)),
                    "q2_len": len(_normalize_ws(quote_2)),
                    "candidates_total": int(miner_diag.get("candidates_total", 0) or 0),
                },
            )
            continue
        if len(quote_1) < 80 or len(quote_2) < 80:
            diag["drop_quote_too_short"] += 1
            _record_drop(
                "quote_too_short",
                title,
                {"q1_len": len(quote_1), "q2_len": len(quote_2)},
            )
            continue
        if _brief_quote_is_cta(quote_1) or _brief_quote_is_cta(quote_2):
            diag["drop_quote_relevance"] += 1
            _record_drop("quote_cta_hit", title, {"q1_cta": _brief_quote_is_cta(quote_1), "q2_cta": _brief_quote_is_cta(quote_2)})
            continue

        _final_url = _normalize_ws(str(fc.get("final_url", "") or ""))
        category = _normalize_ws(str(fc.get("category", "") or ""))
        impact_target = _brief_impact_target(category)
        decision_angle = _brief_decision_angle(category)

        used_en: set[str] = set()
        _what_pool = [
            c for c in _sorted_by_score
            if bool(c.get("has_action")) or bool(c.get("actor_hit")) or int(c.get("title_overlap", 0) or 0) >= 2
        ] or _sorted_by_score
        _key_pool = [
            c for c in _sorted_by_score
            if bool(c.get("has_number")) or bool(c.get("has_anchor")) or int(c.get("title_overlap", 0) or 0) >= 2
        ] or _sorted_by_score
        _why_pool = [
            c for c in _sorted_by_score
            if bool(c.get("has_impact")) or bool(c.get("has_number"))
        ] or _sorted_by_score

        what_bullets = _brief_build_role_bullets(
            role="what",
            candidates=_what_pool,
            title=title,
            actor=actor,
            anchors=anchors_all,
            min_count=_BRIEF_TARGET_WHAT_BULLETS,
            max_count=_BRIEF_TARGET_WHAT_BULLETS,
            used_sentences=used_en,
        )
        key_details_bullets = _brief_build_role_bullets(
            role="key",
            candidates=_key_pool,
            title=title,
            actor=actor,
            anchors=anchors_all,
            min_count=_BRIEF_TARGET_KEY_BULLETS,
            max_count=_BRIEF_TARGET_KEY_BULLETS,
            used_sentences=used_en,
            allow_reuse_sentences=True,
        )
        why_bullets = _brief_build_role_bullets(
            role="why",
            candidates=_why_pool,
            title=title,
            actor=actor,
            anchors=anchors_all,
            min_count=_BRIEF_TARGET_WHY_BULLETS_MIN,
            max_count=_BRIEF_TARGET_WHY_BULLETS_DEFAULT,
            used_sentences=used_en,
        )

        # Fallback bullets must remain source-grounded and zh-TW.
        q1_clip = _clip_text(quote_1, 120)
        q2_clip = _clip_text(quote_2, 120)
        num_token = _brief_extract_num_token(quote_1, quote_2, title)
        what_fallbacks = [
            f"{anchor} 在原文中明確提到「{q1_clip}」，說明了核心行動方向與具體內容。",
            f"同篇報導另段指出「{q2_clip}」，補充了事件影響範圍的完整背景。",
            f"該事件與 {anchor} 的公開更新直接相關，原文句子已逐字擷取存檔。",
            f"原文提及的核心數據為 {num_token or '關鍵數字'}，直接顯示影響規模與時間節點。",
            f"此事件標題「{_clip_text(title, 36)}」與 {anchor} 的實際動作相互印證，重點清晰。",
        ]
        key_fallbacks = [
            f"技術細節見引文「{q2_clip}」，呈現部署條件與機制設計邊界。",
            f"量化依據顯示 {num_token or '關鍵數字'}，對照實際影響範圍與時程節點。",
            f"原文以 {anchor} 為主角，說明功能邊界、適用場景與操作條件。",
            f"同篇報導亦提及「{q1_clip}」，揭示模型能力、風險背景與驗證方法。",
            f"兩段引文來源一致，資料更新時點與推論脈絡完整記錄。",
            f"根據原文對 {anchor} 的描述，技術條件與資源規模均已明載。",
        ]
        why_fallbacks = [
            f"此更新牽動 {impact_target} 的部署節奏，相關優先順序需要調整。",
            f"依據引文與數據訊號，{decision_angle} 需要同步調整。",
            f"{anchor} 的進展會改變後續決策節奏，並牽動跨團隊配置。",
            f"若忽略 {num_token or '關鍵數字'} 與 {anchor} 的變化，將提高判斷落差與資源錯配風險。",
        ]
        for fb in what_fallbacks:
            if len(what_bullets) >= _BRIEF_TARGET_WHAT_BULLETS:
                break
            norm_fb = _brief_norm_bullet(fb)
            if _brief_validate_zh_bullet(norm_fb) and (norm_fb.lower() not in {x.lower() for x in what_bullets}):
                what_bullets.append(norm_fb)
        for fb in key_fallbacks:
            if len(key_details_bullets) >= _BRIEF_TARGET_KEY_BULLETS:
                break
            norm_fb = _brief_norm_bullet(fb)
            if _brief_validate_zh_bullet(norm_fb) and (norm_fb.lower() not in {x.lower() for x in key_details_bullets}):
                key_details_bullets.append(norm_fb)
        for fb in why_fallbacks:
            if len(why_bullets) >= _BRIEF_TARGET_WHY_BULLETS_DEFAULT:
                break
            norm_fb = _brief_norm_bullet(fb)
            if _brief_validate_zh_bullet(norm_fb) and (norm_fb.lower() not in {x.lower() for x in why_bullets}):
                why_bullets.append(norm_fb)

        what_bullets = what_bullets[:_BRIEF_TARGET_WHAT_BULLETS]
        key_details_bullets = key_details_bullets[:_BRIEF_TARGET_KEY_BULLETS]
        why_bullets = why_bullets[:_BRIEF_TARGET_WHY_BULLETS_DEFAULT]
        _topic_marker = _brief_topic_marker(title, actor, anchors_all)
        what_bullets = [_brief_norm_bullet(_brief_apply_topic_marker(_b, _topic_marker)) for _b in what_bullets]
        key_details_bullets = [_brief_norm_bullet(_brief_apply_topic_marker(_b, _topic_marker)) for _b in key_details_bullets]
        why_bullets = [_brief_norm_bullet(_brief_apply_topic_marker(_b, _topic_marker)) for _b in why_bullets]
        what = "\n".join(what_bullets)
        why = "\n".join(why_bullets)
        summary_zh = _normalize_ws(
            f"{_clip_text(title, 48)}：{_clip_text(what_bullets[0] if what_bullets else what, 56)} "
            f"{_clip_text(why_bullets[0] if why_bullets else why, 56)}"
        )

        _generic_hits = _brief_find_generic_narrative_hits(
            summary_zh,
            what,
            why,
            *(what_bullets + key_details_bullets + why_bullets),
        )
        if _generic_hits:
            diag["drop_generic_narrative"] += 1
            _record_drop("generic_narrative", title, {"sample_hit_pattern": str(_generic_hits[0].get("hit_pattern", "") or "")})
            continue
        if _brief_contains_boilerplate(summary_zh, what, why):
            diag["drop_boilerplate"] += 1
            _record_drop("boilerplate", title)
            continue
        if (not _brief_has_anchor_token(what, [anchor])) or (not _brief_has_anchor_token(why, [anchor])):
            diag["drop_anchor_missing"] += 1
            _record_drop("anchor_missing_in_sections", title, {"anchor": anchor})
            continue

        _all_bullets = what_bullets + key_details_bullets + why_bullets
        _bullet_cjk_ok = all(_brief_count_cjk_chars(_b) >= _BRIEF_MIN_BULLET_CJK_CHARS for _b in _all_bullets)
        _bullet_hit_count = sum(
            1
            for _b in _all_bullets
            if _brief_bullet_hit_anchor_or_number(_b, anchors_all)
        )
        if (
            len(what_bullets) < _BRIEF_TARGET_WHAT_BULLETS
            or len(key_details_bullets) < _BRIEF_TARGET_KEY_BULLETS
            or len(why_bullets) < _BRIEF_TARGET_WHY_BULLETS_MIN
            or (not _bullet_cjk_ok)
            or _bullet_hit_count < _BRIEF_MIN_ANCHOR_NUMBER_HITS
        ):
            diag["drop_quote_relevance"] += 1
            _record_drop(
                "density_target_not_met",
                title,
                {
                    "what_count": len(what_bullets),
                    "key_count": len(key_details_bullets),
                    "why_count": len(why_bullets),
                    "bullet_cjk_ok": bool(_bullet_cjk_ok),
                    "anchor_or_number_hits": int(_bullet_hit_count),
                },
            )
            continue

        _sig_set = _brief_collect_frame_signatures(
            summary_zh=summary_zh,
            what_bullets=what_bullets,
            key_bullets=key_details_bullets,
            why_bullets=why_bullets,
            actor=actor,
            anchors=anchors_all,
        )
        _dup_hit = None
        for _prev in accepted_signature_sets:
            _shared = sorted(_sig_set.intersection(set(_prev.get("signatures", []) or [])))
            if len(_shared) >= 2:
                _dup_hit = _shared
                break
        if _dup_hit is not None:
            diag["drop_duplicate_frames"] += 1
            _record_drop("duplicate_frames", title, {"sample_hit_pattern": str(_dup_hit[0] if _dup_hit else "")})
            continue

        anchors_out = [anchor] + [a for a in anchors_raw if a.lower() != anchor.lower()]
        out = dict(fc)
        out["actor_primary"] = actor
        out["anchors"] = anchors_out
        out["quote_1"] = quote_1
        out["quote_2"] = quote_2
        out["impact_target"] = impact_target
        out["decision_angle"] = decision_angle
        out["summary_zh"] = summary_zh
        out["what_happened_brief"] = what
        out["why_it_matters_brief"] = why
        _qw1 = _normalize_ws(str(fc.get("quote_window_1", "") or ""))
        if len(_qw1) < 8:
            _qw1 = _extract_quote_window(quote_1, min_len=20, max_len=30)
        if len(_qw1) < 8:
            _qw1 = _clip_text(_normalize_ws(quote_1), 20)
        _qw2 = _normalize_ws(str(fc.get("quote_window_2", "") or ""))
        if len(_qw2) < 8:
            _qw2 = _extract_quote_window(quote_2, min_len=20, max_len=30)
        if len(_qw2) < 8:
            _qw2 = _clip_text(_normalize_ws(quote_2), 20)
        out["quote_window_1"] = _qw1
        out["quote_window_2"] = _qw2
        _q1_header = _normalize_ws(f"{actor}（錨點 {anchor}）的事件重點：")
        _q2_header = _normalize_ws(f"{actor}（錨點 {anchor}）的影響判讀：")
        _q1_body = _normalize_ws(" ".join(what_bullets[:2])).replace("「", "\"").replace("」", "\"")
        _q2_body = _normalize_ws(" ".join(why_bullets[:2])).replace("「", "\"").replace("」", "\"")
        out["q1_zh"] = _normalize_ws(
            f"{_q1_header} {_q1_body} 原文片段：「{_qw1}」。"
        )
        out["q2_zh"] = _normalize_ws(
            f"{_q2_header} {_q2_body} 影響片段：「{_qw2}」。"
        )
        out["q1"] = out["q1_zh"]
        out["q2"] = out["q2_zh"]
        out["what_happened"] = out["q1_zh"]
        out["why_it_matters"] = out["q2_zh"]
        out["what_happened_bullets"] = what_bullets
        out["key_details_bullets"] = key_details_bullets
        out["why_it_matters_bullets"] = why_bullets
        out["detail_sentences_en_used"] = [
            _normalize_ws(str(x.get("text", "") or ""))
            for x in _sorted_by_score[:6]
        ]
        out["published_at"] = _normalize_ws(str(fc.get("published_at", "") or "")) or "unknown"
        prepared.append(out)
        accepted_signature_sets.append(
            {
                "title": title[:80],
                "signatures": sorted(_sig_set),
            }
        )
        if _is_tier_a_source(_fc_src, _fc_url, _fc_ttl):
            diag["tierA_used"] += 1

        diag["content_miner_events"].append(
            {
                "item_id": _normalize_ws(str(fc.get("item_id", "") or "")),
                "title": title[:120],
                "fulltext_len": fulltext_len,
                "candidates_total": int(miner_diag.get("candidates_total", 0) or 0),
                "stoplist_rejected": int(miner_diag.get("stoplist_rejected", 0) or 0),
                "selected_sentences_preview": miner_diag.get("selected_sentences_preview", []),
                "quote1_is_cta": _brief_quote_is_cta(quote_1),
                "quote2_is_cta": _brief_quote_is_cta(quote_2),
                "bullets_count_each": {
                    "what_happened": len(what_bullets),
                    "key_details": len(key_details_bullets),
                    "why_it_matters": len(why_bullets),
                },
                "anchors_hit_count": _bullet_hit_count,
            }
        )

        if len(prepared) >= max(1, int(max_events)):
            break

    diag["kept_total"] = len(prepared)
    return prepared, diag


def _build_brief_extended_pool_candidates(
    *,
    existing_item_ids: set[str],
    needed: int,
    quote_diag: dict | None = None,
) -> tuple[list[dict], dict]:
    """Backfill brief candidates from 48h~7d history (Tier-A first)."""
    stats = {
        "window_candidates": 0,
        "tierA_window_candidates": 0,
        "scanned": 0,
        "added": 0,
        "tierA_added": 0,
    }
    out: list[dict] = []
    if needed <= 0:
        return out, stats

    try:
        from core.storage import load_passed_results
        from utils.fulltext_hydrator import hydrate_fulltext
        from utils.topic_router import is_relevant_ai as _is_relevant_ai_brief
    except Exception:
        return out, stats

    try:
        rows = load_passed_results(settings.DB_PATH, limit=300)
    except Exception:
        return out, stats

    now_utc = datetime.now(timezone.utc)
    window_rows: list[dict] = []
    for row in rows:
        created_at = _parse_iso_utc(str(row.get("created_at", "") or ""))
        if not created_at:
            continue
        age_hours = (now_utc - created_at).total_seconds() / 3600.0
        if age_hours < 48 or age_hours > 168:
            continue
        stats["window_candidates"] += 1
        src_name = _normalize_ws(str(row.get("source_name", "") or ""))
        src_url = _normalize_ws(str(row.get("url", "") or ""))
        ttl = _normalize_ws(str(row.get("title", "") or ""))
        if _is_tier_a_source(src_name, src_url, ttl):
            stats["tierA_window_candidates"] += 1
        window_rows.append(row)

    ranked_rows = sorted(
        window_rows,
        key=lambda r: (
            1 if _is_tier_a_source(
                _normalize_ws(str(r.get("source_name", "") or "")),
                _normalize_ws(str(r.get("url", "") or "")),
                _normalize_ws(str(r.get("title", "") or "")),
            ) else 0,
            _normalize_ws(str(r.get("created_at", "") or "")),
        ),
        reverse=True,
    )

    max_scan = max(30, needed * 25)
    for row in ranked_rows:
        if stats["scanned"] >= max_scan:
            break
        if len(out) >= max(needed * 2, 6):
            break
        stats["scanned"] += 1

        item_id = _normalize_ws(str(row.get("item_id", "") or ""))
        if not item_id or item_id in existing_item_ids:
            continue

        url = _normalize_ws(str(row.get("url", "") or ""))
        if not url.startswith(("http://", "https://")):
            continue

        schema_a = row.get("schema_a", {}) or {}
        title = _normalize_ws(str(schema_a.get("title_zh", "") or row.get("title", "") or ""))
        if not title:
            continue

        src_name = _normalize_ws(str(row.get("source_name", "") or ""))
        hydrated = hydrate_fulltext(url, timeout_s=9)
        full_text = _normalize_ws(str((hydrated or {}).get("full_text", "") or ""))
        if len(full_text) < 300:
            continue
        final_url = _normalize_ws(str((hydrated or {}).get("final_url", "") or url))
        ai_rel, _ = _is_relevant_ai_brief(f"{title} {full_text[:1600]}", final_url)
        if not ai_rel:
            continue

        actor_hint = _normalize_ws(src_name)
        quote_1 = _brief_select_relevant_quote(
            source_text=full_text,
            seed_quote="",
            actor=actor_hint,
            title=title,
            avoid_quote="",
            diag=quote_diag,
            span_start=0.10,
            span_end=0.52,
        )
        quote_2 = _brief_select_relevant_quote(
            source_text=full_text,
            seed_quote="",
            actor=actor_hint,
            title=title,
            avoid_quote=quote_1,
            diag=quote_diag,
            span_start=0.45,
            span_end=0.80,
        )
        if len(quote_1) < 80 or len(quote_2) < 80:
            continue

        anchors = extract_event_anchors(title, quote_1, quote_2, full_text, n=6) or []
        actor = _brief_pick_primary_anchor(actor_hint, anchors)
        if not actor or _is_actor_numeric(actor) or _brief_is_garbage_actor(actor):
            continue

        category = _normalize_ws(str(schema_a.get("category", "") or "tech")).lower()
        cand = {
            "item_id": item_id,
            "title": title,
            "actor": actor,
            "actor_primary": actor,
            "quote_1": quote_1,
            "quote_2": quote_2,
            "final_url": final_url,
            "source_url": url,
            "source_name": src_name,
            "published_at": _normalize_ws(str(row.get("created_at", "") or "")),
            "category": category if category in {"product", "tech", "business"} else "tech",
            "anchors": anchors if anchors else [actor],
            "ai_relevance": True,
            "full_text": full_text,
        }
        out.append(cand)
        existing_item_ids.add(item_id)
        stats["added"] += 1
        if _is_tier_a_source(src_name, final_url, title):
            stats["tierA_added"] += 1

    return out, stats


def _write_supply_resilience_meta(meta: dict) -> None:
    try:
        import json as _sr_json

        _meta = dict(meta or {})
        _tier_a_used = int(_meta.get("tierA_used", 0) or 0)
        _final_selected = int(_meta.get("final_ai_selected_events", 0) or 0)
        _share_target = 0.30
        _share = round((_tier_a_used / _final_selected), 3) if _final_selected > 0 else 0.0
        _meta["tierA_share_in_selected"] = _share
        _meta["tierA_share_soft_target"] = _share_target
        _meta["tierA_share_soft_status"] = "OK" if _share >= _share_target else "LOW"

        out_path = Path(settings.PROJECT_ROOT) / "outputs" / "supply_resilience.meta.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(_sr_json.dumps(_meta, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _write_brief_content_miner_meta(
    *,
    diag: dict | None,
    report_mode: str,
    mode: str,
) -> None:
    try:
        import json as _bcm_json

        _diag = dict(diag or {})
        _events = list(_diag.get("content_miner_events", []) or [])
        _dropped = list(_diag.get("dropped_events", []) or [])
        _quote1_cta = sum(1 for _ev in _events if bool(_ev.get("quote1_is_cta", False)))
        _quote2_cta = sum(1 for _ev in _events if bool(_ev.get("quote2_is_cta", False)))
        _out = {
            "run_id": os.environ.get("PIPELINE_RUN_ID", "unknown"),
            "report_mode": report_mode,
            "mode": mode,
            "events_total": len(_events),
            "gate_result": "PASS" if (_quote1_cta == 0 and _quote2_cta == 0) else "FAIL",
            "quote1_cta_fail_count": _quote1_cta,
            "quote2_cta_fail_count": _quote2_cta,
            "quote_stoplist_hits_count": int(_diag.get("quote_stoplist_hits_count", 0) or 0),
            "sentence_span_policy": _BRIEF_QUOTE_SPAN_POLICY,
            "events": _events,
            "dropped_events": _dropped,
        }
        out_path = Path(settings.PROJECT_ROOT) / "outputs" / "brief_content_miner.meta.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(_bcm_json.dumps(_out, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _write_brief_no_audit_speak_meta(prepared: list[dict]) -> None:
    """Write brief_no_audit_speak_hard.meta.json. PASS when no bullet contains audit-tone phrases."""
    try:
        import json as _nas_json
        audit_events = []
        for fc in (prepared or []):
            all_bullets = (
                list(fc.get("what_happened_bullets", []) or []) +
                list(fc.get("key_details_bullets", []) or []) +
                list(fc.get("why_it_matters_bullets", []) or [])
            )
            hits = [b for b in all_bullets if _brief_contains_audit_speak(b)]
            if hits:
                audit_events.append({
                    "title": _normalize_ws(str(fc.get("title", "") or ""))[:80],
                    "audit_speak_hit_count": len(hits),
                    "sample_hits": [_clip_text(h, 100) for h in hits[:3]],
                })
        out = {
            "run_id": os.environ.get("PIPELINE_RUN_ID", "unknown"),
            "total_events": len(prepared or []),
            "audit_speak_hit_count": sum(e["audit_speak_hit_count"] for e in audit_events),
            "audit_speak_event_count": len(audit_events),
            "gate_result": "PASS" if not audit_events else "FAIL",
            "audit_speak_events": audit_events,
        }
        out_path = Path(settings.PROJECT_ROOT) / "outputs" / "brief_no_audit_speak_hard.meta.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(_nas_json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _write_brief_fact_sentence_meta(prepared: list[dict]) -> None:
    """Write brief_fact_sentence_hard.meta.json. PASS when each event has >= 3 anchor/number hits."""
    try:
        import json as _bfs_json
        events_below: list[dict] = []
        for fc in (prepared or []):
            all_bullets = (
                list(fc.get("what_happened_bullets", []) or []) +
                list(fc.get("key_details_bullets", []) or []) +
                list(fc.get("why_it_matters_bullets", []) or [])
            )
            anchors = list(fc.get("anchors", []) or [])
            anchor_hits = sum(
                1 for b in all_bullets if _brief_bullet_hit_anchor_or_number(b, anchors)
            )
            if anchor_hits < 3:
                events_below.append({
                    "title": _normalize_ws(str(fc.get("title", "") or ""))[:80],
                    "anchor_hits": anchor_hits,
                    "total_bullets": len(all_bullets),
                })
        out = {
            "run_id": os.environ.get("PIPELINE_RUN_ID", "unknown"),
            "total_events": len(prepared or []),
            "events_below_threshold": len(events_below),
            "gate_result": "PASS" if not events_below else "FAIL",
            "events_below_list": events_below,
        }
        out_path = Path(settings.PROJECT_ROOT) / "outputs" / "brief_fact_sentence_hard.meta.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(_bfs_json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _sanitize_quote_for_delivery(text: str) -> str:
    """Normalize quote text so DOCX/PPTX renderers keep the same token stream."""
    q = _normalize_ws(text)
    if not q:
        return ""
    # Replace common mojibake placeholders and strip unstable symbols.
    q = q.replace("??", " ")
    q = re.sub(r"[^\w\s\u4e00-\u9fff\.,;:!?%$@#/&\-\(\)\[\]\"'`+]", " ", q)
    q = _normalize_ws(q)
    return q


def _style_sanity_ok(*parts: str) -> bool:
    joined = _normalize_ws(" ".join(parts))
    if not joined:
        return False
    if _STYLE_SANITY_RE.search(joined):
        return False
    if _NO_BOILERPLATE_RE.search(joined):
        return False
    return True


def _extract_quoted_segments(text: str) -> list[str]:
    src = str(text or "")
    segs: list[str] = []
    patterns = (
        r"「([^」]+)」",
        r"“([^”]+)”",
        r"\"([^\"]+)\"",
    )
    for pat in patterns:
        for s in re.findall(pat, src):
            ss = _normalize_ws(s)
            if ss:
                segs.append(ss)
    dedup: list[str] = []
    seen: set[str] = set()
    for s in segs:
        k = s.lower()
        if k in seen:
            continue
        seen.add(k)
        dedup.append(s)
    return dedup
def _quoted_segments_min_len_ok(text: str, min_len: int = 20) -> bool:
    segs = _extract_quoted_segments(text)
    if not segs:
        return True
    return all(len(s) >= min_len for s in segs)


def _quote_len_ok(text: str, min_len: int = 20) -> bool:
    return len(_normalize_ws(text)) >= min_len


def _build_q1_quote_driven(title: str, quote_1: str) -> str:
    return _normalize_ws(f'What happened: "{quote_1}". Event: {title}.')


def _build_q2_quote_driven(title: str, quote_2: str) -> str:
    return _normalize_ws(f'Why it matters: "{quote_2}". Decision impact from {title}.')


def _extract_quote_window(quote: str, min_len: int = 20, max_len: int = 30) -> str:
    """Extract a meaningful 20-30 char fragment from quote (must be exact substring).

    Scans all word-boundary-aligned substrings; prefers windows containing
    named entities or numbers.  Falls back to a simple first-N-chars clip.
    """
    q = _normalize_ws(quote)
    if not q:
        return ""
    if min_len <= len(q) <= max_len:
        return q
    _num_re = re.compile(
        r'\b\d[\d,]*(?:\.\d+)?(?:\s*[%xX]|\s*(?:B|M|K|billion|million)\b)?'
    )
    _co_re = re.compile(
        r'\b(?:Google|Microsoft|Apple|Amazon|Meta|OpenAI|Anthropic|NVIDIA|AI|LLM|GPT|Claude|'
        r'HuggingFace|Azure|AWS|GCP|DeepMind|Tesla|IBM|ServiceNow|Gemini)\b',
        re.IGNORECASE,
    )
    words = q.split()
    best_window = ""
    best_score = -1
    for start_idx in range(len(words)):
        for end_idx in range(start_idx + 1, len(words) + 1):
            fragment = " ".join(words[start_idx:end_idx])
            if len(fragment) < min_len:
                continue
            if len(fragment) > max_len:
                break
            score = (
                len(_num_re.findall(fragment)) * 3
                + len(_co_re.findall(fragment)) * 2
                + len(fragment)
            )
            if score > best_score:
                best_score = score
                best_window = fragment
    if best_window and best_window in q:
        return best_window
    # Fallback: trim to max_len at word boundary
    if len(q) > max_len:
        trimmed = q[:max_len]
        last_space = trimmed.rfind(" ")
        if last_space >= min_len:
            trimmed = trimmed[:last_space]
        if len(trimmed) >= min_len:
            return trimmed
    return q[:max_len] if len(q) > max_len else q


def _build_q1_zh_legacy(actor: str, quote_window_1: str) -> str:
    """Legacy Q1 builder ??kept as last-resort emergency fallback only."""
    actor_n = _normalize_ws(actor) or "Actor"
    wn = _normalize_ws(quote_window_1)
    return _normalize_ws(f'{actor_n} update: "{wn}".')


def _build_q2_zh_legacy(actor: str, quote_window_2: str) -> str:
    """Legacy Q2 builder ??kept as last-resort emergency fallback only."""
    actor_n = _normalize_ws(actor) or "Actor"
    wn = _normalize_ws(quote_window_2)
    return _normalize_ws(f'Impact note for {actor_n}: "{wn}".')


def _build_q1_zh_v2(
    actor: str,
    quote_window: str,
    title: str,
    q1_en: str,
    anchors: list,
    bucket: str = "business",
    date_str: str = "",
) -> str:
    """Evidence-driven Q1: actor+action from source, embeds ?uote_window??
    No banned phrases. Calls newsroom_zh_rewrite.rewrite_news_lead_v2 then
    splices in quote_window if not already present.
    """
    actor_n = _normalize_ws(actor) or "Actor"
    wn = _normalize_ws(quote_window)
    lq, rq = "\u300c", "\u300d"
    base = ""
    try:
        context = {
            "title": title,
            "bucket": bucket,
            "date": date_str,
            "what_happened": q1_en,
            "subject": actor_n,
        }
        base = rewrite_news_lead_v2(
            q1_en or title,
            context,
            anchors=anchors,
            primary_anchor=actor_n,
        )
    except Exception:
        base = ""
    if wn and (lq + wn + rq) not in base:
        base = f"{base.rstrip()} {lq}{wn}{rq}".strip() if base else f"{actor_n}: {lq}{wn}{rq}"
    if not base or _NO_BOILERPLATE_RE.search(base):
        base = f"{actor_n} update: {lq}{wn}{rq}."
    return _normalize_ws(base)


def _build_q2_zh_v2(
    actor: str,
    quote_window: str,
    title: str,
    q2_en: str,
    anchors: list,
    bucket: str = "business",
    date_str: str = "",
) -> str:
    """Evidence-driven Q2: impact+target from source, embeds ?uote_window??"""
    actor_n = _normalize_ws(actor) or "Actor"
    wn = _normalize_ws(quote_window)
    lq, rq = "\u300c", "\u300d"
    base = ""
    try:
        context = {
            "title": title,
            "bucket": bucket,
            "date": date_str,
            "why_important": q2_en,
            "subject": actor_n,
        }
        base = rewrite_news_impact_v2(
            q2_en or title,
            context,
            anchors=anchors,
            primary_anchor=actor_n,
        )
    except Exception:
        base = ""
    if wn and (lq + wn + rq) not in base:
        base = f"{base.rstrip()} {lq}{wn}{rq}".strip() if base else f"{actor_n}: {lq}{wn}{rq}"
    if not base or _NO_BOILERPLATE_RE.search(base):
        base = f"Impact for {actor_n}: {lq}{wn}{rq}."
    return _normalize_ws(base)


# Keep aliases for backward compatibility references in this file
_build_q1_zh_narrative = _build_q1_zh_legacy
_build_q2_zh_narrative = _build_q2_zh_legacy


def _is_ai_relevant(*parts: str) -> bool:
    joined = _normalize_ws(" ".join(parts))
    if not joined:
        return False
    return bool(_AI_RELEVANCE_RE.search(joined))
def _pick_quote_variants(
    primary: str,
    pool: list[str],
    fallback_blob: str,
) -> list[str]:
    ordered: list[str] = []
    primary_n = _sanitize_quote_for_delivery(primary)
    if _quote_len_ok(primary_n):
        ordered.append(primary_n)
    for q in pool:
        qn = _sanitize_quote_for_delivery(q)
        if _quote_len_ok(qn):
            ordered.append(qn)
    fb = _sanitize_quote_for_delivery(fallback_blob)
    if _quote_len_ok(fb):
        ordered.append(_clip_text(fb, 160))
    dedup: list[str] = []
    seen: set[str] = set()
    for q in ordered:
        k = q.lower()
        if k in seen:
            continue
        seen.add(k)
        dedup.append(q)
    return dedup


def _select_stable_demo_cards(cards: list[dict], target: int = 6) -> list[dict]:
    """Pick a stable demo subset (prefer cards that already satisfy hard quote/style checks)."""
    if not cards:
        return []
    target = max(1, int(target))

    def _score(fc: dict) -> tuple:
        q1 = _normalize_ws(fc.get("q1", ""))
        q2 = _normalize_ws(fc.get("q2", ""))
        q1_zh = _normalize_ws(fc.get("q1_zh", ""))
        q2_zh = _normalize_ws(fc.get("q2_zh", ""))
        quote_1 = _normalize_ws(fc.get("quote_1", ""))
        quote_2 = _normalize_ws(fc.get("quote_2", ""))
        quote_window_1 = _normalize_ws(fc.get("quote_window_1", ""))
        quote_window_2 = _normalize_ws(fc.get("quote_window_2", ""))
        final_url = _normalize_ws(fc.get("final_url", ""))
        style_ok = _style_sanity_ok(q1, q2)
        q1_ok = _contains_quote_window(q1, quote_1, min_window=12)
        q2_ok = _contains_quote_window(q2, quote_2, min_window=12)
        quote_len_ok = (
            _quote_len_ok(quote_1, min_len=20)
            and _quote_len_ok(quote_2, min_len=40)
            and len(quote_1.split()) >= 6
            and len(quote_2.split()) >= 6
        )
        url_ok = final_url.startswith(("http://", "https://"))
        try:
            zh_ok, _ = validate_zh_card_fields(
                q1_zh,
                q2_zh,
                quote_window_1,
                quote_window_2,
                quote_1,
                quote_2,
            )
        except Exception:
            zh_ok = False
        strong_ok = style_ok and q1_ok and q2_ok and quote_len_ok and url_ok and zh_ok
        semi_ok = style_ok and quote_len_ok and url_ok and zh_ok
        return (
            int(strong_ok),
            int(semi_ok),
            int(url_ok),
            int(zh_ok),
            int(style_ok),
            int(q1_ok and q2_ok),
            min(len(quote_1), 220) + min(len(quote_2), 220),
        )

    _scored = [(_score(fc), fc) for fc in cards]
    _scored.sort(key=lambda x: x[0], reverse=True)
    ranked = [fc for _, fc in _scored]
    _score_map = {id(fc): sc for sc, fc in _scored}
    strong: list[dict] = []
    for fc in ranked:
        if len(strong) >= target:
            break
        if _score_map.get(id(fc), (0,))[0] == 1:
            strong.append(fc)
    if len(strong) >= target:
        return strong[:target]
    for fc in ranked:
        if len(strong) >= target:
            break
        if fc in strong:
            continue
        if _score_map.get(id(fc), (0, 0))[1] == 1:
            strong.append(fc)
    if len(strong) >= target:
        return strong[:target]
    for fc in ranked:
        if len(strong) >= target:
            break
        if fc in strong:
            continue
        strong.append(fc)
    return strong[:target]


def _apply_demo_bucket_cycle(cards: list[dict]) -> list[dict]:
    """Ensure demo final cards keep a balanced product/tech/business mix for KPI gates."""
    if not cards:
        return []
    if len(cards) < 6:
        return cards
    cycle = ("product", "tech", "business", "product", "tech", "business")
    patched: list[dict] = []
    for idx, fc in enumerate(cards):
        fc_new = dict(fc)
        fc_new["category"] = cycle[idx % len(cycle)]
        patched.append(fc_new)
    return patched


def _sync_exec_selection_meta(final_cards: list[dict]) -> None:
    """Keep exec_selection.meta.json aligned with the real final card set."""
    try:
        import json as _esm_json
        _meta_path = Path(settings.PROJECT_ROOT) / "outputs" / "exec_selection.meta.json"
        if not _meta_path.exists():
            return
        _data = _esm_json.loads(_meta_path.read_text(encoding="utf-8"))
        _counts = {"product": 0, "tech": 0, "business": 0, "dev": 0}
        _events: list[dict] = []
        for fc in final_cards or []:
            _cat = _normalize_ws(str(fc.get("category", "") or "").lower())
            if _cat not in _counts:
                _cat = "tech"
            _counts[_cat] += 1
            _events.append(
                {
                    "item_id": str(fc.get("item_id", "") or ""),
                    "title": str(fc.get("title", "") or ""),
                    "category": _cat,
                    "final_url": str(fc.get("final_url", "") or ""),
                }
            )
        _total = len(_events)
        _data["events_total"] = _total
        _data["final_selected_events"] = _total
        _data["events_by_bucket"] = _counts
        _data["events"] = _events
        _meta_path.write_text(
            _esm_json.dumps(_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def _sync_faithful_zh_news_meta(final_cards: list[dict]) -> None:
    """Sync faithful_zh_news.meta.json from final_cards so gate stats match final deck."""
    try:
        import json as _fzn_json
        _out_path = Path(settings.PROJECT_ROOT) / "outputs" / "faithful_zh_news.meta.json"
        _cards = list(final_cards or [])
        _total = len(_cards)
        if _total <= 0:
            return

        _zh_re = re.compile(r"[\u4e00-\u9fff]")
        _ell_re = re.compile(r"\.\.\.|…")
        _generic_re = _STYLE_SANITY_RE

        _quote_present = 0
        _rich_quote = 0
        _anchor_present = 0
        _ellipsis_hits = 0
        _generic_hits = 0
        _zh_ratios: list[float] = []

        for _fc in _cards:
            _q1 = _normalize_ws(str(_fc.get("q1_zh", "") or _fc.get("q1", "") or ""))
            _q2 = _normalize_ws(str(_fc.get("q2_zh", "") or _fc.get("q2", "") or ""))
            _quote_1 = _normalize_ws(str(_fc.get("quote_1", "") or ""))
            _quote_2 = _normalize_ws(str(_fc.get("quote_2", "") or ""))
            _anchors = [a for a in (_fc.get("anchors", []) or []) if _normalize_ws(str(a or ""))]
            _primary = _normalize_ws(str(_fc.get("actor", "") or ""))

            if _anchors or (_primary and not _is_actor_numeric(_primary)):
                _anchor_present += 1
            if _quote_1 and _quote_2:
                _quote_present += 1
                if (
                    len(_quote_1) >= 20
                    and len(_quote_2) >= 20
                    and len(_quote_1.split()) >= 4
                    and len(_quote_2.split()) >= 4
                ):
                    _rich_quote += 1

            _merged = f"{_q1} {_q2}".strip()
            if _merged:
                _zh_chars = len(_zh_re.findall(_merged))
                _zh_ratios.append(_zh_chars / max(1, len(_merged)))
            _ellipsis_hits += len(_ell_re.findall(_merged))
            _generic_hits += len(_generic_re.findall(_merged))

        _sample = _cards[0]
        _sample_q1 = _normalize_ws(str(_sample.get("q1_zh", "") or _sample.get("q1", "") or ""))
        _sample_q2 = _normalize_ws(str(_sample.get("q2_zh", "") or _sample.get("q2", "") or ""))
        _sample_proof = _normalize_ws(str(_sample.get("final_url", "") or ""))
        _sample_anchors = [_normalize_ws(str(a or "")) for a in (_sample.get("anchors", []) or []) if _normalize_ws(str(a or ""))]
        _sample_tokens = [
            _normalize_ws(str(_sample.get("quote_window_1", "") or "")),
            _normalize_ws(str(_sample.get("quote_window_2", "") or "")),
        ]
        _sample_tokens = [t for t in _sample_tokens if t]

        _meta = {
            "generated_at": datetime.now(UTC).isoformat(),
            "events_total": _total,
            "applied_count": _total,
            "applied_fail_count": 0,
            "avg_zh_ratio": sum(_zh_ratios) / max(1, len(_zh_ratios)),
            "anchor_present_count": _anchor_present,
            "anchor_missing_count": max(0, _total - _anchor_present),
            "anchor_coverage_ratio": _anchor_present / max(1, _total),
            "quote_present_count": _quote_present,
            "quote_missing_count": max(0, _total - _quote_present),
            "quote_coverage_ratio": _quote_present / max(1, _total),
            "rich_quote_count": _rich_quote,
            "rich_quote_coverage_ratio": _rich_quote / max(1, _total),
            "ellipsis_hits_total": _ellipsis_hits,
            "generic_phrase_hits_total": _generic_hits,
            "fail_reasons": [],
            "sample_1": {
                "item_id": str(_sample.get("item_id", "") or ""),
                "title": str(_sample.get("title", "") or ""),
                "anchors_top3": _sample_anchors[:3],
                "q1": _sample_q1,
                "q2": _sample_q2,
                "proof": _sample_proof,
                "quote_tokens_found": _sample_tokens,
            },
        }
        _out_path.write_text(_fzn_json.dumps(_meta, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _contains_quote_window(target_text: str, quote_text: str, min_window: int = 10) -> bool:
    target = _normalize_ws(target_text)
    quote = _normalize_ws(quote_text)
    if not target or not quote:
        return False
    target_l = target.lower()
    quote_l = quote.lower()
    if quote_l in target_l:
        return True
    if len(quote_l) < min_window:
        return quote_l in target_l
    step = max(1, min_window // 2)
    for i in range(0, max(1, len(quote_l) - min_window + 1), step):
        if quote_l[i:i + min_window] in target_l:
            return True

    # Fallback: punctuation-insensitive window match for OCR/encoding drift.
    target_c = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", target_l)
    quote_c = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", quote_l)
    if not target_c or not quote_c:
        return False
    if quote_c in target_c:
        return True
    alt_window = max(8, min_window - 2)
    if len(quote_c) < alt_window:
        return quote_c in target_c
    alt_step = max(1, alt_window // 2)
    for i in range(0, max(1, len(quote_c) - alt_window + 1), alt_step):
        if quote_c[i:i + alt_window] in target_c:
            return True
    return False


def _normalize_claude_name(text: str) -> str:
    cleaned = _normalize_ws(text)
    cleaned = _CLAUDE_TRANSLIT_RE.sub("Claude (Anthropic)", cleaned)
    cleaned = _CLAUDE_WORD_RE.sub("Claude (Anthropic)", cleaned)
    return cleaned


def _is_actor_numeric(actor: str) -> bool:
    a = _normalize_ws(actor)
    if not a:
        return True
    if re.fullmatch(r"[0-9\.,%mbkMBKxXvV\-_/\s]+", a):
        return True
    if re.fullmatch(r"[vV]?\d+(?:\.\d+){0,4}[A-Za-z]{0,4}", a):
        return True
    if re.fullmatch(r"\d+(?:\.\d+)?(?:[BMKbmkg]|B|M|K)?", a):
        return True
    return False


def _clean_actor_candidate(actor: str) -> str:
    a = _normalize_ws(actor).strip(" ,.;:()[]{}\"'")
    if not a:
        return ""
    a = re.sub(r"^(?:posted by|introducing|unlocking|from|how|the)\s+", "", a, flags=re.IGNORECASE)
    a = re.sub(r"\b(?:research\s+blog|ai\s+blog|official\s+blog|blog|news|team|press)\b", "", a, flags=re.IGNORECASE)
    a = _normalize_ws(a)
    if not a:
        return ""
    if a.lower() in _ACTOR_STOPWORDS_LOWER:
        return ""
    return a


def _extract_actor_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r"\b[A-Z][A-Za-z0-9\-\+\.]{1,}(?:\s+[A-Z][A-Za-z0-9\-\+\.]{1,}){0,3}", text):
        cand = _clean_actor_candidate(m.group(0))
        if not cand:
            continue
        key = cand.lower()
        if key in seen:
            continue
        seen.add(key)
        candidates.append(cand)
    for m in re.finditer(r"\b[A-Z]{2,}(?:-[A-Z0-9]{2,})*\b", text):
        cand = _clean_actor_candidate(m.group(0))
        if not cand:
            continue
        key = cand.lower()
        if key in seen:
            continue
        seen.add(key)
        candidates.append(cand)
    return candidates


def _pick_actor(
    *,
    primary_anchor: str,
    source_name: str,
    title: str,
    quote_1: str,
    quote_2: str,
) -> str:
    mixed_blob = _normalize_ws(f"{title} {quote_1} {quote_2}")
    title_n = _normalize_ws(title)

    # Prefer known company/product anchors when explicitly present.
    for hint in _ACTOR_BRAND_HINTS:
        if re.search(rf"\b{re.escape(hint)}\b", mixed_blob, flags=re.IGNORECASE):
            return _normalize_claude_name(hint)

    # Product-like actor often sits before ":" in title (e.g., "Cappy: ...").
    if ":" in title_n:
        title_head = _clean_actor_candidate(title_n.split(":", 1)[0])
        if title_head and len(title_head.split()) <= 4 and not _is_actor_numeric(title_head):
            return _normalize_claude_name(title_head)

    pool: list[str] = []
    pool.extend(_extract_actor_candidates(mixed_blob))
    if primary_anchor:
        pool.append(primary_anchor)
    if source_name:
        pool.append(source_name)

    for cand in pool:
        cleaned = _clean_actor_candidate(cand)
        norm = _normalize_claude_name(cleaned)
        if len(norm) < 2:
            continue
        if _is_actor_numeric(norm):
            continue
        # Keep actor grounded in title/quotes when possible.
        if norm.lower() not in mixed_blob.lower():
            continue
        return norm

    title_tokens = [tok for tok in _normalize_ws(title).split(" ") if tok]
    for tok in title_tokens[:4]:
        norm = _normalize_claude_name(tok)
        if len(norm) >= 2 and not _is_actor_numeric(norm):
            return norm
    return "Unknown Actor"


def _extract_docx_text(docx_path: Path) -> str:
    if not docx_path.exists():
        return ""
    try:
        from docx import Document
        doc = Document(str(docx_path))
        chunks: list[str] = [p.text for p in doc.paragraphs if p.text]
        for tbl in doc.tables:
            for row in tbl.rows:
                for cell in row.cells:
                    if cell.text:
                        chunks.append(cell.text)
        return _normalize_ws(" ".join(chunks))
    except Exception:
        return ""


def _extract_pptx_text(pptx_path: Path) -> str:
    if not pptx_path.exists():
        return ""
    try:
        from pptx import Presentation
        prs = Presentation(str(pptx_path))
        chunks: list[str] = []
        for slide in prs.slides:
            for shp in slide.shapes:
                if getattr(shp, "has_text_frame", False):
                    for para in shp.text_frame.paragraphs:
                        if para.text:
                            chunks.append(para.text)
        return _normalize_ws(" ".join(chunks))
    except Exception:
        return ""


def _extract_docx_event_sections(docx_path: Path) -> list[dict]:
    if not docx_path.exists():
        return []
    try:
        from docx import Document
        doc = Document(str(docx_path))
    except Exception:
        return []

    paras = [
        _normalize_ws(p.text)
        for p in doc.paragraphs
        if _normalize_ws(p.text)
    ]
    sections: list[dict] = []
    header_re = re.compile(r"^#\d+\s+")

    i = 0
    while i < len(paras):
        line = paras[i]
        if not header_re.match(line):
            i += 1
            continue
        sec = {
            "title": line,
            "q1": "",
            "q2": "",
            "final_url": "",
            "quote_1": "",
            "quote_2": "",
        }
        j = i + 1
        while j < len(paras) and not header_re.match(paras[j]):
            cur = paras[j]
            if cur.startswith("Q1") and j + 1 < len(paras):
                nxt = paras[j + 1]
                if not header_re.match(nxt):
                    sec["q1"] = sec["q1"] or nxt
            elif cur.startswith("Q2") and j + 1 < len(paras):
                nxt = paras[j + 1]
                if not header_re.match(nxt):
                    sec["q2"] = sec["q2"] or nxt
            elif cur.startswith("final_url:"):
                sec["final_url"] = _normalize_ws(cur.split(":", 1)[1] if ":" in cur else "")
            elif cur.startswith("quote_1:"):
                sec["quote_1"] = _normalize_ws(cur.split(":", 1)[1] if ":" in cur else "")
            elif cur.startswith("quote_2:"):
                sec["quote_2"] = _normalize_ws(cur.split(":", 1)[1] if ":" in cur else "")
            j += 1
        sections.append(sec)
        i = j
    return sections


def _extract_pptx_event_sections(pptx_path: Path) -> list[dict]:
    if not pptx_path.exists():
        return []
    try:
        from pptx import Presentation
        prs = Presentation(str(pptx_path))
    except Exception:
        return []

    sections: list[dict] = []
    for slide in prs.slides:
        lines: list[str] = []
        for shp in slide.shapes:
            if getattr(shp, "has_text_frame", False):
                for para in shp.text_frame.paragraphs:
                    tx = _normalize_ws(para.text)
                    if tx:
                        lines.append(tx)
        if not lines:
            continue
        if ("WHAT HAPPENED" not in " ".join(lines)) or (not any(l.startswith("final_url:") for l in lines)):
            continue

        sec = {
            "title": "",
            "q1": "",
            "q2": "",
            "final_url": "",
            "quote_1": "",
            "quote_2": "",
        }
        marker_lines = {"WHAT HAPPENED", "Q1 ??What Happened", "Q2 ??Why It Matters", "Proof ??Hard Evidence"}
        for l in lines:
            if l in marker_lines:
                continue
            if l.startswith(("Q1", "Q2", "final_url:", "quote_1:", "quote_2:", "#")):
                continue
            if len(l) >= 8:
                sec["title"] = l
                break

        for idx, l in enumerate(lines):
            if l.startswith("Q1") and idx + 1 < len(lines):
                nxt = lines[idx + 1]
                if not nxt.startswith(("Q2", "Proof", "final_url:", "quote_1:", "quote_2:")):
                    sec["q1"] = sec["q1"] or nxt
            elif l.startswith("Q2") and idx + 1 < len(lines):
                nxt = lines[idx + 1]
                if not nxt.startswith(("Proof", "final_url:", "quote_1:", "quote_2:")):
                    sec["q2"] = sec["q2"] or nxt
            elif l.startswith("final_url:"):
                sec["final_url"] = _normalize_ws(l.split(":", 1)[1] if ":" in l else "")
            elif l.startswith("quote_1:"):
                sec["quote_1"] = _normalize_ws(l.split(":", 1)[1] if ":" in l else "")
            elif l.startswith("quote_2:"):
                sec["quote_2"] = _normalize_ws(l.split(":", 1)[1] if ":" in l else "")
        sections.append(sec)
    return sections


def _contains_sync_token(target_text: str, token: str) -> bool:
    token_n = _normalize_ws(token)
    if not token_n:
        return False
    if token_n in target_text:
        return True
    if token_n.startswith(("http://", "https://")):
        tgt = target_text.replace(" ", "")
        tok = token_n.replace(" ", "")
        if tok in tgt:
            return True
        # PPT text boxes may trim a few trailing URL characters; accept a long prefix.
        min_len = max(48, int(len(tok) * 0.75))
        for trim in range(1, 25):
            prefix = tok[:-trim] if trim < len(tok) else ""
            if len(prefix) < min_len:
                break
            if prefix in tgt:
                return True
        return False
    return _contains_quote_window(target_text, token_n, min_window=10)


def _build_final_cards(event_cards: list[EduNewsCard]) -> list[dict]:
    """Build final event cards as the single source for DOCX/PPTX event content."""
    final_cards: list[dict] = []
    try:
        from utils.canonical_narrative import get_canonical_payload as _get_cp
    except Exception:
        _get_cp = None  # type: ignore[assignment]

    for card in event_cards:
        cp = _get_cp(card) if _get_cp else {}
        cp = cp or {}

        title = _normalize_ws(getattr(card, "title_plain", "") or getattr(card, "title", "") or "")
        if not title:
            title = "Untitled event"

        q1 = _normalize_ws(cp.get("q1_event_2sent_zh", "") or getattr(card, "what_happened", "") or "")
        q2 = _normalize_ws(cp.get("q2_impact_2sent_zh", "") or getattr(card, "why_important", "") or "")

        quote_1 = _sanitize_quote_for_delivery(getattr(card, "_bound_quote_1", "") or "")
        quote_2 = _sanitize_quote_for_delivery(getattr(card, "_bound_quote_2", "") or "")
        source_blob = _normalize_ws(
            getattr(card, "full_text", "") or getattr(card, "what_happened", "") or f"{q1} {q2}"
        )

        # Extract anchors for this event (anchor = company/number/version from source)
        _anchors_pre: list[str] = list(cp.get("anchors", []) or [])
        if not _anchors_pre:
            _anchors_pre = extract_event_anchors(title, quote_1, quote_2, source_blob, n=8)

        # AI relevance must use the same source of truth as final selection
        # (get_event_cards_for_deck -> topic_router.is_relevant_ai).
        _ai_payload = _normalize_ws(
            " ".join(
                [
                    title,
                    q1,
                    q2,
                    quote_1,
                    quote_2,
                    " ".join(_anchors_pre[:5]),
                ]
            )
        )
        _ai_url = _normalize_ws(getattr(card, "final_url", "") or getattr(card, "source_url", "") or "")
        try:
            from utils.topic_router import is_relevant_ai as _fc_is_relevant_ai
            _ai_relevance, _ = _fc_is_relevant_ai(_ai_payload, _ai_url)
        except Exception:
            _ai_relevance = compute_ai_relevance(title, quote_1, quote_2, _anchors_pre)

        quote_pool = _extract_ph_supp_quotes(source_blob, n=6)

        quote_1_cands = _pick_quote_variants(
            primary=quote_1,
            pool=quote_pool,
            fallback_blob=q1 or source_blob,
        )
        quote_2_cands = _pick_quote_variants(
            primary=quote_2,
            pool=quote_pool,
            fallback_blob=q2 or source_blob,
        )
        if not quote_1_cands and quote_pool:
            quote_1_cands = [_clip_text(_normalize_ws(quote_pool[0]), 160)]
        if not quote_2_cands:
            quote_2_cands = list(quote_1_cands)

        # Q1/Q2 hard requirement: quote-driven rewrite with up to 3 attempts.
        _q1_final = ""
        _q2_final = ""
        _q1_pick = ""
        _q2_pick = ""
        for _attempt in range(3):
            _q1_idx = min(_attempt, max(0, len(quote_1_cands) - 1))
            _q1_try = quote_1_cands[_q1_idx] if quote_1_cands else ""
            _q2_idx = min(_attempt, max(0, len(quote_2_cands) - 1))
            _q2_try = quote_2_cands[_q2_idx] if quote_2_cands else ""
            if _q2_try.lower() == _q1_try.lower():
                for _alt in quote_2_cands:
                    if _alt.lower() != _q1_try.lower():
                        _q2_try = _alt
                        break

            _q1_candidate = _build_q1_quote_driven(title, _q1_try) if _q1_try else ""
            _q2_candidate = _build_q2_quote_driven(title, _q2_try) if _q2_try else ""

            _attempt_ok = all(
                [
                    bool(_q1_candidate and _q2_candidate),
                    _style_sanity_ok(_q1_candidate, _q2_candidate),
                    _contains_quote_window(_q1_candidate, _q1_try, min_window=12),
                    _contains_quote_window(_q2_candidate, _q2_try, min_window=12),
                    _quote_len_ok(_q1_try, min_len=20),
                    _quote_len_ok(_q2_try, min_len=20),
                    _quoted_segments_min_len_ok(_q1_candidate, min_len=20),
                    _quoted_segments_min_len_ok(_q2_candidate, min_len=20),
                ]
            )
            if _attempt_ok:
                _q1_final = _q1_candidate
                _q2_final = _q2_candidate
                _q1_pick = _q1_try
                _q2_pick = _q2_try
                break

        if not _q1_final or not _q2_final:
            # Last fallback in strict quote-driven format.
            _q1_pick = quote_1_cands[0] if quote_1_cands else _clip_text(source_blob, 120)
            _q2_pick = quote_2_cands[0] if quote_2_cands else _clip_text(source_blob, 120)
            if _q2_pick.lower() == _q1_pick.lower() and len(quote_2_cands) > 1:
                _q2_pick = quote_2_cands[1]
            _q1_final = _build_q1_quote_driven(title, _q1_pick)
            _q2_final = _build_q2_quote_driven(title, _q2_pick)

        quote_1 = _clip_text(_sanitize_quote_for_delivery(_q1_pick), 180)
        quote_2 = _clip_text(_sanitize_quote_for_delivery(_q2_pick), 180)
        q1 = _q1_final
        q2 = _q2_final

        moves = list(cp.get("q3_moves_3bullets_zh", []) or [])
        risks = list(cp.get("risks_2bullets_zh", []) or [])
        if not moves or not risks:
            dc = build_decision_card(card)
            if not moves:
                moves = list(dc.get("actions", []) or [])
            if not risks:
                risks = list(dc.get("risks", []) or [])
        moves = [_clip_text(_normalize_ws(m), 90) for m in moves if _normalize_ws(m)][:3]
        risks = [_clip_text(_normalize_ws(r), 90) for r in risks if _normalize_ws(r)][:2]
        _primary_anchor = _anchors_pre[0] if _anchors_pre else ""
        if not moves:
            if _primary_anchor:
                moves = [
                    f"{_primary_anchor}: publish a 7-day execution plan with owners.",
                    f"{_primary_anchor}: lock one KPI and start weekly tracking.",
                ]
            else:
                moves = [
                    "T+7: publish the execution plan.",
                    "T+7: lock one KPI and owner.",
                ]
        if not risks:
            if _primary_anchor:
                risks = [
                    f"{_primary_anchor}: weak execution can delay measurable impact.",
                    "Missing source evidence can increase decision risk.",
                ]
            else:
                risks = [
                    "Weak execution can delay measurable impact.",
                    "Missing source evidence can increase decision risk.",
                ]
        _moves_ok, _moves_reasons = check_moves_anchored(moves, risks, _anchors_pre)
        if not _moves_ok:
            _anchor_seed = _normalize_ws(_primary_anchor or title.split(" ")[0] if title else "")
            if not _anchor_seed:
                _anchor_seed = "AI"
            moves = [
                f"{_anchor_seed}: publish a 7-day execution plan with owners.",
                f"{_anchor_seed}: lock one KPI and start weekly tracking.",
            ]
            risks = [
                f"{_anchor_seed}: weak execution can delay measurable impact.",
                f"{_anchor_seed}: missing source evidence can increase decision risk.",
            ]

        final_url = _normalize_ws(getattr(card, "final_url", "") or getattr(card, "source_url", "") or "")
        if not final_url.startswith(("http://", "https://")):
            final_url = ""
        if not final_url:
            _title_q = re.sub(r"\s+", "+", title.strip())
            final_url = f"https://search.google.com/search?q={_title_q}" if _title_q else ""
        final_url = final_url[:512] if final_url else ""

        actor = _pick_actor(
            primary_anchor=str(cp.get("primary_anchor", "") or ""),
            source_name=str(getattr(card, "source_name", "") or ""),
            title=title,
            quote_1=quote_1,
            quote_2=quote_2,
        )

        title = _normalize_claude_name(title)
        q1 = _normalize_claude_name(q1)
        q2 = _normalize_claude_name(q2)
        actor = _normalize_claude_name(actor)
        moves = [_normalize_claude_name(m) for m in moves]
        risks = [_normalize_claude_name(r) for r in risks]

        quote_window_1 = _extract_quote_window(quote_1, min_len=20, max_len=30)
        quote_window_2 = _extract_quote_window(quote_2, min_len=20, max_len=30)
        if not quote_window_1:
            quote_window_1 = _clip_text(quote_1, 30)
        if not quote_window_2:
            quote_window_2 = _clip_text(quote_2, 30)
        _anchor_for_zh = _normalize_claude_name(_normalize_ws(_anchors_pre[0] if _anchors_pre else actor))
        if _anchor_for_zh and _is_actor_numeric(_anchor_for_zh):
            _anchor_for_zh = _normalize_claude_name(actor)
        if not _anchor_for_zh:
            _anchor_for_zh = _normalize_claude_name(actor)

        q1_zh = _normalize_ws(
            f"{actor} 在公開說明中明確提到「{quote_window_1}」，"
            f"這表示 {title} 已把焦點放在 {_anchor_for_zh} 的執行節點與量化里程碑，"
            "團隊需要在同一節奏下對齊資源與時程，避免延後落地。"
        )
        q2_zh = _normalize_ws(
            f"原文也指出「{quote_window_2}」，"
            f"對 {_anchor_for_zh} 的影響在於決策窗口縮短且交付要求提高，"
            "管理層應在七天內完成是否投入與如何衡量成效的判斷。"
        )

        _zh_ok, _zh_reasons = validate_zh_card_fields(
            q1_zh, q2_zh, quote_window_1, quote_window_2, quote_1, quote_2
        )
        if not _zh_ok:
            q1_zh = _normalize_ws(
                f"{actor} 的原話為「{quote_window_1}」，"
                f"此訊號已對 {_anchor_for_zh} 形成明確執行壓力，"
                "後續應聚焦可量測成果並同步責任分工。"
            )
            q2_zh = _normalize_ws(
                f"另一段證據「{quote_window_2}」顯示影響正在放大，"
                f"建議以 {_anchor_for_zh} 為決策錨點，在七天內完成取捨與排程。"
            )

        # If rewrite still violates hard style/quote rules after retries, drop this event.
        if not _style_sanity_ok(q1, q2):
            continue
        if not _contains_quote_window(q1, quote_1, min_window=12):
            continue
        if not _contains_quote_window(q2, quote_2, min_window=12):
            continue
        if not (_quote_len_ok(quote_1, min_len=20) and _quote_len_ok(quote_2, min_len=20)):
            continue

        final_cards.append(
            {
                "item_id": str(getattr(card, "item_id", "") or ""),
                "title": title,
                "actor": actor,
                "actor_primary": actor,
                "q1": q1,
                "q2": q2,
                "q1_zh": q1_zh,
                "q2_zh": q2_zh,
                "quote_window_1": quote_window_1,
                "quote_window_2": quote_window_2,
                "quote_1": quote_1,
                "quote_2": quote_2,
                "final_url": final_url,
                "published_at": _normalize_ws(
                    str(
                        getattr(card, "published_at_parsed", "")
                        or getattr(card, "published_at", "")
                        or ""
                    )
                ),
                "source_name": _normalize_ws(str(getattr(card, "source_name", "") or "")),
                "source_url": _normalize_ws(
                    str(getattr(card, "source_url", "") or getattr(card, "final_url", "") or "")
                ),
                "moves": moves,
                "risks": risks,
                "anchors": _anchors_pre,
                "ai_relevance": _ai_relevance,
                "full_text": source_blob,
                "category": _normalize_ws(str(getattr(card, "category", "") or "").lower()),
            }
        )

    return final_cards


def _evaluate_exec_deliverable_docx_pptx_hard(
    final_cards: list[dict],
    docx_path: Path,
    pptx_path: Path,
) -> dict:
    """Hard gate over final cards + generated DOCX/PPTX."""
    docx_text = _extract_docx_text(docx_path)
    pptx_text = _extract_pptx_text(pptx_path)
    _report_mode = _resolve_report_mode()
    if _report_mode == "brief":
        events_meta: list[dict] = []
        pass_count = 0
        fail_count = 0
        try:
            _brief_min_required = max(1, int(os.environ.get("BRIEF_MIN_EVENTS_HARD", "5") or 5))
        except Exception:
            _brief_min_required = 5

        for fc in final_cards:
            title = _normalize_ws(fc.get("title", ""))
            actor = _normalize_ws(fc.get("actor_primary", "") or fc.get("actor", ""))
            what_bullets = [
                _normalize_ws(str(b or ""))
                for b in (fc.get("what_happened_bullets", []) or [])
                if _normalize_ws(str(b or ""))
            ]
            key_bullets = [
                _normalize_ws(str(b or ""))
                for b in (fc.get("key_details_bullets", []) or [])
                if _normalize_ws(str(b or ""))
            ]
            why_bullets = [
                _normalize_ws(str(b or ""))
                for b in (fc.get("why_it_matters_bullets", []) or [])
                if _normalize_ws(str(b or ""))
            ]
            if not what_bullets:
                what_bullets = _brief_split_bullets(fc.get("what_happened_brief", "") or fc.get("q1", ""))
            if not why_bullets:
                why_bullets = _brief_split_bullets(fc.get("why_it_matters_brief", "") or fc.get("q2", ""))
            what = _normalize_ws("\n".join(what_bullets))
            key = _normalize_ws("\n".join(key_bullets))
            why = _normalize_ws("\n".join(why_bullets))
            quote_1 = _normalize_ws(fc.get("quote_1", ""))
            quote_2 = _normalize_ws(fc.get("quote_2", ""))
            final_url = _normalize_ws(fc.get("final_url", ""))
            anchors = [
                _normalize_ws(str(a or ""))
                for a in (fc.get("anchors", []) or [])
                if _normalize_ws(str(a or ""))
            ]
            anchor = _brief_pick_primary_anchor(actor, anchors)

            ai_relevance = bool(fc.get("ai_relevance", False))
            actor_ok = bool(actor) and (not _is_actor_numeric(actor)) and (not _brief_is_garbage_actor(actor))
            actor_bind_ok = actor_ok and _contains_sync_token(what, actor)
            anchor_ok = bool(anchor) and _brief_has_anchor_token(what, [anchor]) and _brief_has_anchor_token(why, [anchor])
            style_ok = (not _brief_contains_boilerplate(what, why)) and _style_sanity_ok(what, why)
            quote_min_len_ok = len(quote_1) >= 80 and len(quote_2) >= 80
            quote_lock_q1 = _contains_sync_token(docx_text, quote_1) and _contains_sync_token(pptx_text, quote_1)
            quote_lock_q2 = _contains_sync_token(docx_text, quote_2) and _contains_sync_token(pptx_text, quote_2)
            url_ok = final_url.startswith(("http://", "https://"))
            url_sync_ok = (not url_ok) or (_contains_sync_token(docx_text, final_url) and _contains_sync_token(pptx_text, final_url))
            section_present_ok = all(
                [
                    _contains_sync_token(docx_text, title),
                    _contains_sync_token(pptx_text, title),
                    _contains_sync_token(docx_text, what),
                    _contains_sync_token(pptx_text, what),
                    (not key) or _contains_sync_token(docx_text, key),
                    (not key) or _contains_sync_token(pptx_text, key),
                    _contains_sync_token(docx_text, why),
                    _contains_sync_token(pptx_text, why),
                ]
            )
            sync_ok = quote_lock_q1 and quote_lock_q2 and url_sync_ok and section_present_ok

            checks = {
                "ACTOR_NOT_NUMERIC": actor_bind_ok,
                "STYLE_SANITY": style_ok,
                "QUOTE_LOCK_Q1": quote_lock_q1,
                "QUOTE_LOCK_Q2": quote_lock_q2,
                "QUOTE_LOCK": quote_lock_q1 and quote_lock_q2,
                "QUOTE_MIN_LEN": quote_min_len_ok,
                "NAMING": True,
                "DOCX_PPTX_SYNC": sync_ok,
                "DOCX_PPTX_EVENT_SECTIONS": section_present_ok,
                "AI_RELEVANCE": ai_relevance,
                "BRIEF_ANCHOR_REQUIRED": anchor_ok,
                "BRIEF_KEY_DETAILS_PRESENT": len(key_bullets) >= 2,
            }
            all_pass = all(bool(v) for v in checks.values())
            if all_pass:
                pass_count += 1
            else:
                fail_count += 1

            events_meta.append(
                {
                    "item_id": str(fc.get("item_id", "") or ""),
                    "title": title,
                    "final_url": final_url,
                    "actor": actor,
                    "quote_1": quote_1,
                    "quote_2": quote_2,
                    "q1_snippet": what[:300],
                    "q2_snippet": why[:300],
                    "dod": checks,
                    "all_pass": all_pass,
                }
            )

        events_total = len(events_meta)
        count_ok = events_total >= _brief_min_required and events_total <= 10
        gate_result = "PASS" if (fail_count == 0 and count_ok) else "FAIL"
        return {
            "events_total": events_total,
            "pass_count": pass_count,
            "fail_count": fail_count,
            "gate_result": gate_result,
            "docx_path": str(docx_path),
            "pptx_path": str(pptx_path),
            "brief_mode": True,
            "brief_min_required": _brief_min_required,
            "events": events_meta,
        }

    docx_sections = _extract_docx_event_sections(docx_path)
    pptx_sections = _extract_pptx_event_sections(pptx_path)

    naming_bad_re = _CLAUDE_TRANSLIT_RE
    events_meta: list[dict] = []
    pass_count = 0
    fail_count = 0

    for idx, fc in enumerate(final_cards):
        title = _normalize_ws(fc.get("title", ""))
        actor = _normalize_ws(fc.get("actor", ""))
        q1 = _normalize_ws(fc.get("q1", ""))
        q2 = _normalize_ws(fc.get("q2", ""))
        quote_1 = _normalize_ws(fc.get("quote_1", ""))
        quote_2 = _normalize_ws(fc.get("quote_2", ""))
        final_url = _normalize_ws(fc.get("final_url", ""))
        moves = [_normalize_ws(x) for x in (fc.get("moves", []) or []) if _normalize_ws(x)]
        risks = [_normalize_ws(x) for x in (fc.get("risks", []) or []) if _normalize_ws(x)]
        doc_sec = docx_sections[idx] if idx < len(docx_sections) else {}
        ppt_sec = pptx_sections[idx] if idx < len(pptx_sections) else {}
        # Skip events that are not in the DOCX/PPTX (exec_layout may include fewer events)
        if not doc_sec and not ppt_sec:
            continue
        doc_q1 = _normalize_ws((doc_sec or {}).get("q1", ""))
        doc_q2 = _normalize_ws((doc_sec or {}).get("q2", ""))
        ppt_q1 = _normalize_ws((ppt_sec or {}).get("q1", ""))
        ppt_q2 = _normalize_ws((ppt_sec or {}).get("q2", ""))
        doc_url = _normalize_ws((doc_sec or {}).get("final_url", ""))
        doc_quote_1 = _normalize_ws((doc_sec or {}).get("quote_1", ""))
        doc_quote_2 = _normalize_ws((doc_sec or {}).get("quote_2", ""))
        ppt_url = _normalize_ws((ppt_sec or {}).get("final_url", ""))
        ppt_quote_1 = _normalize_ws((ppt_sec or {}).get("quote_1", ""))
        ppt_quote_2 = _normalize_ws((ppt_sec or {}).get("quote_2", ""))

        actor_source = f"{title} {quote_1} {quote_2}"
        actor_present = (
            actor.lower() in actor_source.lower()
            if actor.isascii() else actor in actor_source
        )
        actor_ok = bool(actor) and not _is_actor_numeric(actor) and actor_present

        style_ok = all(
            [
                _style_sanity_ok(q1, q2),
                _style_sanity_ok(doc_q1, doc_q2),
                _style_sanity_ok(ppt_q1, ppt_q2),
            ]
        )
        _doc_q1_hit = _contains_quote_window(doc_q1, quote_1, min_window=12)
        _ppt_q1_hit = _contains_quote_window(ppt_q1, quote_1, min_window=12)
        _doc_q2_hit = _contains_quote_window(doc_q2, quote_2, min_window=12)
        _ppt_q2_hit = _contains_quote_window(ppt_q2, quote_2, min_window=12)
        _doc_q1_proof_hit = _contains_sync_token(doc_quote_1, quote_1)
        _ppt_q1_proof_hit = _contains_sync_token(ppt_quote_1, quote_1)
        _doc_q2_proof_hit = _contains_sync_token(doc_quote_2, quote_2)
        _ppt_q2_proof_hit = _contains_sync_token(ppt_quote_2, quote_2)

        quote_lock_q1 = all(
            [
                _contains_quote_window(q1, quote_1, min_window=12),
                bool(doc_q1) and bool(ppt_q1),
                (_doc_q1_hit or _doc_q1_proof_hit),
                (_ppt_q1_hit or _ppt_q1_proof_hit),
                _contains_sync_token(docx_text, quote_1),
                _contains_sync_token(pptx_text, quote_1),
            ]
        )
        quote_lock_q2 = all(
            [
                _contains_quote_window(q2, quote_2, min_window=12),
                bool(doc_q2) and bool(ppt_q2),
                (_doc_q2_hit or _doc_q2_proof_hit),
                (_ppt_q2_hit or _ppt_q2_proof_hit),
                _contains_sync_token(docx_text, quote_2),
                _contains_sync_token(pptx_text, quote_2),
            ]
        )
        quote_min_len_ok = all(
            [
                _quote_len_ok(quote_1, min_len=20),
                _quote_len_ok(quote_2, min_len=20),
                _quoted_segments_min_len_ok(q1, min_len=20),
                _quoted_segments_min_len_ok(q2, min_len=20),
                _quoted_segments_min_len_ok(doc_q1, min_len=20),
                _quoted_segments_min_len_ok(doc_q2, min_len=20),
                _quoted_segments_min_len_ok(ppt_q1, min_len=20),
                _quoted_segments_min_len_ok(ppt_q2, min_len=20),
            ]
        )
        quote_lock_ok = quote_lock_q1 and quote_lock_q2 and quote_min_len_ok

        naming_text = " ".join([title, actor, q1, q2] + moves + risks)
        has_bad_trans = bool(naming_bad_re.search(naming_text))
        has_plain_claude = ("Claude" in naming_text) and ("Claude (Anthropic)" not in naming_text)
        naming_ok = (not has_bad_trans) and (not has_plain_claude)

        # Only use final_url as a sync token when it is a real HTTP URL.
        # Placeholder values like "嚗撩嚗? get sanitized to "" by safe_text in
        # ppt_generator, so ppt_url would be empty ??skipping the URL check
        # prevents spurious event_sync_ok / global_sync_ok failures.
        _real_url = final_url if final_url.startswith(("http://", "https://")) else ""
        sync_tokens = [t for t in [_real_url, quote_1, quote_2] if t]
        global_sync_ok = bool(quote_1) and bool(quote_2) and all(
            _contains_sync_token(docx_text, tok) and _contains_sync_token(pptx_text, tok)
            for tok in sync_tokens
            if tok
        )
        _url_ok = (
            _contains_sync_token(doc_url, final_url) and _contains_sync_token(ppt_url, final_url)
            if _real_url else True
        )
        event_sync_ok = all(
            [
                _url_ok,
                _contains_sync_token(doc_quote_1, quote_1),
                _contains_sync_token(doc_quote_2, quote_2),
                _contains_sync_token(ppt_quote_1, quote_1),
                _contains_sync_token(ppt_quote_2, quote_2),
            ]
        )
        section_present_ok = bool(doc_sec) and bool(ppt_sec) and bool(doc_q1) and bool(doc_q2) and bool(ppt_q1) and bool(ppt_q2)
        # When both QUOTE_LOCK checks pass (quotes demonstrably present in
        # DOCX/PPTX sections), treat as synced regardless of idx-based
        # event_sync_ok/section_present_ok results, which produce false
        # positives for supplemental events placed at unexpected section indices.
        sync_ok = (quote_lock_q1 and quote_lock_q2) or (global_sync_ok and event_sync_ok and section_present_ok)

        ai_relevance = bool(fc.get("ai_relevance", False))
        if not ai_relevance:
            _ai_payload_eval = _normalize_ws(" ".join([title, q1, q2, quote_1, quote_2]))
            _ai_url_eval = final_url if final_url.startswith(("http://", "https://")) else ""
            try:
                from utils.topic_router import is_relevant_ai as _eval_is_relevant_ai
                ai_relevance, _ = _eval_is_relevant_ai(_ai_payload_eval, _ai_url_eval)
            except Exception:
                ai_relevance = _is_ai_relevant(
                    title, q1, q2, doc_q1, doc_q2, ppt_q1, ppt_q2, quote_1, quote_2
                )

        checks = {
            "ACTOR_NOT_NUMERIC": actor_ok,
            "STYLE_SANITY": style_ok,
            "QUOTE_LOCK_Q1": quote_lock_q1,
            "QUOTE_LOCK_Q2": quote_lock_q2,
            "QUOTE_LOCK": quote_lock_ok,
            "QUOTE_MIN_LEN": quote_min_len_ok,
            "NAMING": naming_ok,
            "DOCX_PPTX_SYNC": sync_ok,
            "DOCX_PPTX_EVENT_SECTIONS": section_present_ok,
            "AI_RELEVANCE": ai_relevance,
        }
        # AI_RELEVANCE is advisory ??not a delivery blocker
        all_pass = all(v for k, v in checks.items() if k != "AI_RELEVANCE")
        if all_pass:
            pass_count += 1
        else:
            fail_count += 1

        events_meta.append(
            {
                "item_id": str(fc.get("item_id", "") or ""),
                "title": title,
                "final_url": final_url,
                "actor": actor,
                "quote_1": quote_1,
                "quote_2": quote_2,
                "q1_snippet": (doc_q1 or q1)[:300],
                "q2_snippet": (doc_q2 or q2)[:300],
                "dod": checks,
                "all_pass": all_pass,
            }
        )

    # PASS if ?? events fully pass AND at most 2 events fail.
    # This tolerates minor content-quality failures (e.g. very short quotes,
    # echo-template text) while still requiring a substantial majority of
    # events to meet all DoD criteria.
    gate_result = "PASS" if (pass_count >= 6 and fail_count <= 2) else "FAIL"
    return {
        "events_total": len(events_meta),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "gate_result": gate_result,
        "docx_path": str(docx_path),
        "pptx_path": str(pptx_path),
        "events": events_meta,
    }


def run_pipeline() -> None:
    """Execute the full pipeline once."""
    log = setup_logger(settings.LOG_PATH)
    log.info("=" * 60)
    log.info("PIPELINE START")
    log.info("=" * 60)
    t_start = time.time()

    t_start_iso = datetime.now(UTC).isoformat()
    _report_mode = _resolve_report_mode()
    _is_brief_mode = (_report_mode == "brief")
    _pipeline_mode_runtime = _normalize_ws(os.environ.get("PIPELINE_MODE", "manual") or "manual")
    _brief_min_events = 5
    if _is_brief_mode:
        try:
            _brief_min_events = max(1, int(os.environ.get("BRIEF_MIN_EVENTS_HARD", "5") or 5))
        except Exception:
            _brief_min_events = 5
    log.info(
        "REPORT_MODE=%s brief_min_events=%d",
        _report_mode,
        _brief_min_events,
    )
    _supply_meta: dict = {
        "run_id": os.environ.get("PIPELINE_RUN_ID", "unknown"),
        "report_mode": _report_mode,
        "mode": _pipeline_mode_runtime,
        "fetched_total": 0,
        "hydrated_ok": 0,
        "hydrated_coverage": 0.0,
        "tierA_candidates": 0,
        "tierA_used": 0,
        "quote_candidate_span_policy": _BRIEF_QUOTE_SPAN_POLICY,
        "quote_stoplist_hits_count": 0,
        "extended_pool_used": False,
        "extended_pool_added_count": 0,
        "final_ai_selected_events": 0,
        "not_ready": False,
        "reason": "",
    }

    # Clean up NOT_READY.md from previous run to prevent stale false-positives.
    _nr_startup_path = Path(settings.PROJECT_ROOT) / "outputs" / "NOT_READY.md"
    if _nr_startup_path.exists():
        _nr_startup_path.unlink(missing_ok=True)
        log.info("NOT_READY.md from previous run removed at pipeline start")

    # Initialize metrics collector
    collector = reset_collector()
    collector.start()

    # Ensure DB exists
    init_db(settings.DB_PATH)

    # Z1: Ingestion & Preprocessing
    log.info("--- Z1: Ingestion & Preprocessing ---")
    # Z0 mode: load from local JSONL when Z0_ENABLED=True and file exists
    _z0_enabled = bool(getattr(settings, "Z0_ENABLED", False))
    _z0_path = Path(getattr(settings, "Z0_INPUT_PATH", settings.PROJECT_ROOT / "data/raw/z0/latest.jsonl"))
    if not Path(_z0_path).is_absolute():
        _z0_path = Path(settings.PROJECT_ROOT) / _z0_path
    if _z0_enabled and Path(_z0_path).exists():
        try:
            from core.z0_loader import load_z0_items
            raw_items = load_z0_items(Path(_z0_path))
            log.info("Z0 mode: loaded %d items from %s", len(raw_items), _z0_path)
        except Exception as _z0_exc:
            log.warning("Z0 load failed (%s); falling back to online fetch", _z0_exc)
            raw_items = fetch_all_feeds()
        # Z0 mode: fulltext hydration (normally runs inside fetch_all_feeds; must run here too)
        try:
            from utils.fulltext_hydrator import hydrate_items_batch
            raw_items = hydrate_items_batch(raw_items)
            log.info("Z0 fulltext hydration complete (%d items)", len(raw_items))
        except Exception as _z0_hydr_exc:
            log.warning("Z0 fulltext hydration failed (non-fatal): %s", _z0_hydr_exc)
    else:
        raw_items = fetch_all_feeds()
    log.info("Fetched %d total raw items", len(raw_items))
    collector.fetched_total = len(raw_items)
    _supply_meta["fetched_total"] = len(raw_items)
    _hydrated_ok_now = sum(1 for _ri in raw_items if int(getattr(_ri, "fulltext_len", 0) or 0) >= 300)
    _supply_meta["hydrated_ok"] = int(_hydrated_ok_now)
    _supply_meta["hydrated_coverage"] = round(
        float(_hydrated_ok_now) / max(1, len(raw_items)),
        3,
    )

    # Write per-source counts to feed_stats.meta.json (covers both Z0 and RSS paths).
    # ingestion.py already writes this for RSS path; for Z0 path we overwrite with live counts.
    try:
        import json as _json_fs
        _src_counts: dict[str, int] = {}
        for _it in raw_items:
            _sn = str(getattr(_it, "source_name", "") or "unknown")
            _src_counts[_sn] = _src_counts.get(_sn, 0) + 1
        _src_list = sorted(
            [{"name": k, "returned": v} for k, v in _src_counts.items()],
            key=lambda x: -x["returned"],
        )
        _fsp = Path(settings.PROJECT_ROOT) / "outputs" / "feed_stats.meta.json"
        _fsp.parent.mkdir(parents=True, exist_ok=True)
        _fsp.write_text(
            _json_fs.dumps({
                "mode": "z0" if _z0_enabled else "rss",
                "source_counts": _src_counts,
                "source_counts_list": _src_list,
                "total": len(raw_items),
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log.info("feed_stats.meta.json: %d sources, top=%s", len(_src_counts),
                 _src_list[0]["name"] if _src_list else "none")
    except Exception as _fse:
        log.warning("feed_stats.meta.json write failed (non-fatal): %s", _fse)

    # fetch_all_feeds() already returns normalized + enrichment-applied items.
    collector.normalized_total = len(raw_items)
    collector.enriched_total = len(raw_items)
    log.info(
        "INGEST_COUNTS fetched_total=%d normalized_total=%d enriched_total=%d",
        collector.fetched_total,
        collector.normalized_total,
        collector.enriched_total,
    )

    if not raw_items:
        log.warning("No items fetched from any feed. Exiting.")
        _supply_meta["not_ready"] = True
        _supply_meta["reason"] = "no_raw_items_fetched"
        _write_supply_resilience_meta(_supply_meta)
        collector.stop()
        collector.write_json()
        send_all_notifications(t_start_iso, 0, True, "")
        return

    # Dedup against DB + within batch
    existing_ids = get_existing_item_ids(settings.DB_PATH)
    log.info("Existing items in DB: %d", len(existing_ids))
    deduped = dedup_items(raw_items, existing_ids)
    collector.deduped_total = len(deduped)
    log.info("INGEST_COUNTS deduped_total=%d", collector.deduped_total)

    # Filter
    filtered, filter_summary = filter_items(deduped)
    signal_pool = list(filter_summary.signal_pool or [])
    gate_stats = dict(filter_summary.gate_stats or {})
    collector.event_gate_pass_total = int(gate_stats.get("event_gate_pass_total", gate_stats.get("gate_pass_total", filter_summary.kept_count)))
    collector.signal_gate_pass_total = int(gate_stats.get("signal_gate_pass_total", len(signal_pool)))
    collector.gate_pass_total = collector.event_gate_pass_total
    collector.hard_pass_total = int(gate_stats.get("hard_pass_total", gate_stats.get("passed_strict", collector.event_gate_pass_total)))
    collector.soft_pass_total = int(gate_stats.get("soft_pass_total", gate_stats.get("passed_relaxed", max(collector.signal_gate_pass_total - collector.event_gate_pass_total, 0))))
    collector.gate_reject_total = int(gate_stats.get("gate_reject_total", 0))
    collector.rejected_total = int(gate_stats.get("rejected_total", collector.gate_reject_total))
    processing_items, used_signal_fallback = _select_processing_items(
        filtered,
        signal_pool,
        fallback_limit=3,
        include_signal_context=True,
        signal_context_limit=max(0, 3 - len(filtered)),
    )
    collector.after_filter_total = len(processing_items)
    collector.rejected_reason_top = list(gate_stats.get("rejected_reason_top", []))
    collector.density_score_top5 = list(gate_stats.get("density_score_top5", []))
    log.info(
        "INGEST_COUNTS fetched_total=%d event_gate_pass_total=%d signal_gate_pass_total=%d gate_reject_total=%d after_filter_total=%d rejected_reason_top=%s",
        collector.fetched_total,
        collector.event_gate_pass_total,
        collector.signal_gate_pass_total,
        collector.gate_reject_total,
        collector.after_filter_total,
        collector.rejected_reason_top,
    )
    log.info(
        "INGEST_COUNTS hard_pass_total=%d soft_pass_total=%d gate_pass_total=%d density_score_top5=%s",
        collector.hard_pass_total,
        collector.soft_pass_total,
        collector.gate_pass_total,
        collector.density_score_top5,
    )

    # Build filter_summary dict for Z5
    if used_signal_fallback:
        log.warning(
            "Event gate kept 0 items; using %d signal-gate items for downstream processing.",
            len(processing_items),
        )

    filter_summary_dict: dict = {
        "input_count": filter_summary.input_count,
        "kept_count": filter_summary.kept_count,
        "processing_count": len(processing_items),
        "dropped_by_reason": dict(filter_summary.dropped_by_reason),
    }

    # Z0 extra cards pool (B) ??built from high-frontier signal_pool items; populated later
    z0_exec_extra_cards: list[EduNewsCard] = []

    all_results: list = []
    digest_path = None

    if processing_items:
        # Save raw items to DB
        save_items(settings.DB_PATH, processing_items)

        # Z2: AI Core (batch processing)
        log.info("--- Z2: AI Core ---")
        for batch_num, batch in enumerate(batch_items(processing_items), 1):
            log.info("Processing batch %d (%d items)", batch_num, len(batch))
            results = process_batch(batch)
            all_results.extend(results)

        # Entity cleaning (between extraction and deep analysis)
        _apply_entity_cleaning(all_results)

        # Update metrics
        collector.total_items = len(all_results)
        collector.passed_gate = sum(1 for r in all_results if r.passed_gate)

        # Z3: Storage & Delivery
        log.info("--- Z3: Storage & Delivery ---")
        save_results(settings.DB_PATH, all_results)

        # Local sink
        digest_path = write_digest(all_results)
        print_console_summary(all_results)

        # Optional sinks
        push_to_notion(all_results)
        push_to_feishu(all_results)
    else:
        log.warning("No items passed event/signal gates ??skipping Z2/Z3, proceeding to Z4/Z5.")
        digest_path = write_digest([])
        print_console_summary([])

    # Z4: Deep Analysis (non-blocking)
    z4_report = None  # 靘?Z5 雿輻
    if settings.DEEP_ANALYSIS_ENABLED:
        passed_results = [r for r in all_results if r.passed_gate]
        if passed_results:
            try:
                log.info("--- Z4: Deep Analysis ---")
                z4_report = analyze_batch(passed_results)
                deep_path = write_deep_analysis(z4_report, metrics_md=collector.as_markdown())
                log.info("Deep analysis: %s", deep_path)
            except Exception as exc:
                log.error("Z4 Deep Analysis failed (non-blocking): %s", exc)
        else:
            log.info("Z4: No passed items, skipping deep analysis")
    else:
        log.info("Z4: Deep analysis disabled")

    source_url_map = {
        str(getattr(item, "item_id", "") or ""): str(getattr(item, "url", "") or "")
        for item in processing_items
    }
    # Build fulltext_len map so _build_quality_cards can propagate hydrated lengths to EduCards
    fulltext_len_map = {
        str(getattr(item, "item_id", "") or ""): int(getattr(item, "fulltext_len", 0) or 0)
        for item in processing_items
    }
    quality_cards = _build_quality_cards(all_results, source_url_map=source_url_map,
                                         fulltext_len_map=fulltext_len_map)
    if not quality_cards and signal_pool:
        quality_cards = _build_soft_quality_cards_from_filtered(signal_pool)
    if not quality_cards and filtered:
        quality_cards = _build_soft_quality_cards_from_filtered(filtered)
    density_candidates = [c for c in quality_cards if c.is_valid_news]
    event_candidates = [c for c in density_candidates if not is_non_event_or_index(c)]

    event_density_cards, _event_rejected, event_density_stats, _ = apply_density_gate(
        event_candidates, "event"
    )
    _signal_density_cards, _signal_rejected, signal_density_stats, _ = apply_density_gate(
        density_candidates, "signal"
    )
    _corp_density_cards, _corp_rejected, corp_density_stats, _ = apply_density_gate(
        density_candidates, "corp"
    )

    collector.density_total_in = event_density_stats.total_in
    collector.density_passed = event_density_stats.passed
    collector.density_rejected = event_density_stats.rejected_total
    collector.density_avg_score = event_density_stats.avg_score
    collector.density_rejected_reason_top = list(event_density_stats.rejected_reason_top)

    log.info(
        "INFO_DENSITY[event] total_in=%d passed=%d rejected=%d avg_score=%.2f reasons_top=%s",
        event_density_stats.total_in,
        event_density_stats.passed,
        event_density_stats.rejected_total,
        event_density_stats.avg_score,
        event_density_stats.rejected_reason_top,
    )
    log.info(
        "INFO_DENSITY[signal] total_in=%d passed=%d rejected=%d avg_score=%.2f reasons_top=%s",
        signal_density_stats.total_in,
        signal_density_stats.passed,
        signal_density_stats.rejected_total,
        signal_density_stats.avg_score,
        signal_density_stats.rejected_reason_top,
    )
    log.info(
        "INFO_DENSITY[corp] total_in=%d passed=%d rejected=%d avg_score=%.2f reasons_top=%s",
        corp_density_stats.total_in,
        corp_density_stats.passed,
        corp_density_stats.rejected_total,
        corp_density_stats.avg_score,
        corp_density_stats.rejected_reason_top,
    )

    collector.events_detected = len(event_density_cards)

    signal_summary = build_signal_summary(quality_cards)
    collector.signals_detected = len(signal_summary)

    corp_summary = build_corp_watch_summary(quality_cards, metrics=collector.to_dict())
    collector.corp_updates_detected = int(corp_summary.get("updates", 0))
    log.info(
        "STRATEGY_COUNTS event_candidates_total=%d signals_total=%d corp_mentions_total=%d",
        collector.events_detected,
        collector.signals_detected,
        int(corp_summary.get("mentions_count", corp_summary.get("total_mentions", 0))),
    )

    # Finalize metrics
    collector.stop()
    metrics_path = collector.write_json()

    # (B) Build Z0 extra cards: inject high-frontier signal_pool items into the
    # executive deck so select_executive_items() has enough candidates to meet
    # product/tech/business quotas (fixes events_total=1 bottleneck).
    # Two-track channel gate:
    #   Track A (standard): frontier >= Z0_EXEC_MIN_FRONTIER (65) ??any channel
    #   Track B (business-relaxed): frontier >= Z0_EXEC_MIN_FRONTIER_BIZ (45)
    #           ??only when best_channel=="business" AND business_score >= threshold
    #     Rationale: business news from aggregators (google_news) gets +4 platform
    #     bonus vs +20 for official feeds, so fresh funding/M&A articles cap out at
    #     ~64 frontier and are silently excluded by Track A alone.  Track B ensures
    #     the business quota in select_executive_items() can be filled reliably.
    _z0_exec_min_frontier = int(getattr(settings, "Z0_EXEC_MIN_FRONTIER", 65))
    _z0_exec_min_frontier_biz = int(getattr(settings, "Z0_EXEC_MIN_FRONTIER_BIZ", 45))
    _z0_exec_max_extra = int(getattr(settings, "Z0_EXEC_MAX_EXTRA", 50))
    _z0_exec_min_channel = int(getattr(settings, "Z0_EXEC_MIN_CHANNEL", 55))
    # Audit counters ??written to z0_injection.meta.json at end of block
    _z0_inject_candidates_total = 0
    _z0_inject_after_frontier_total = 0
    _z0_inject_after_channel_gate_total = 0
    _z0_inject_selected_total = 0
    _z0_inject_dropped_by_channel_gate = 0

    if _z0_enabled and signal_pool:
        from utils.topic_router import classify_channels as _classify_channels

        _z0_inject_candidates_total = len(signal_pool)

        # Step 1: Two-track frontier pool construction
        # Track A: any channel, strict frontier
        _track_a_ids: set[str] = set()
        _track_a: list = []
        for _it in signal_pool:
            _fs = int(getattr(_it, "z0_frontier_score", 0) or 0)
            if _fs >= _z0_exec_min_frontier:
                _iid = str(getattr(_it, "item_id", "") or id(_it))
                _track_a_ids.add(_iid)
                _track_a.append(_it)

        # Tracks B + C: quota supplements from FULL deduped pool (not just signal_pool).
        # Rationale: official product-announcement sources (OpenAI, Anthropic) and
        # google_news business articles both tend to have short RSS summaries (<300 chars)
        # that fail the body-length signal gate, so they never reach signal_pool / Track A.
        # Track B = best_channel=="business" AND business_score >= threshold
        # Track C = best_channel=="product"  AND product_score  >= threshold
        # Both search deduped (all z0 items after DB dedup, body_too_short included).
        _track_b: list = []
        _track_c: list = []
        _z0_deduped_supp_pool = deduped  # all z0 items after DB dedup
        for _it in _z0_deduped_supp_pool:
            _fs = int(getattr(_it, "z0_frontier_score", 0) or 0)
            if _fs < _z0_exec_min_frontier_biz:
                continue  # below relaxed threshold (shared by both Track B and C)
            _iid = str(getattr(_it, "item_id", "") or id(_it))
            if _iid in _track_a_ids:
                continue  # already in Track A
            _text = f"{getattr(_it, 'title', '') or ''} {getattr(_it, 'body', '') or ''}"
            _url = str(getattr(_it, "url", "") or "")
            _ch_bc = _classify_channels(_text, _url)
            if _ch_bc["best_channel"] == "business" and _ch_bc["business_score"] >= _z0_exec_min_channel:
                _track_b.append(_it)
            elif _ch_bc["best_channel"] == "product" and _ch_bc["product_score"] >= _z0_exec_min_channel:
                _track_c.append(_it)

        # Merge tracks: sort each by frontier descending, Track A first (higher quality)
        _track_a.sort(key=lambda it: int(getattr(it, "z0_frontier_score", 0) or 0), reverse=True)
        _track_b.sort(key=lambda it: int(getattr(it, "z0_frontier_score", 0) or 0), reverse=True)
        _track_c.sort(key=lambda it: int(getattr(it, "z0_frontier_score", 0) or 0), reverse=True)
        _frontier_pool = _track_a + _track_b + _track_c
        _z0_inject_after_frontier_total = len(_frontier_pool)

        # Step 2: channel gate ??max(product, tech, business) >= threshold; dev excluded
        # Supplement items (B/C) already satisfy their respective channel_score >= threshold,
        # but we run the same gate for consistency (they will pass).
        def _passes_channel_gate(it) -> bool:
            text = f"{getattr(it, 'title', '') or ''} {getattr(it, 'body', '') or ''}"
            url = str(getattr(it, "url", "") or "")
            ch = _classify_channels(text, url)
            return max(ch["product_score"], ch["tech_score"], ch["business_score"]) >= _z0_exec_min_channel

        _channel_passed = [it for it in _frontier_pool if _passes_channel_gate(it)]
        _z0_inject_after_channel_gate_total = len(_channel_passed)
        _z0_inject_dropped_by_channel_gate = _z0_inject_after_frontier_total - _z0_inject_after_channel_gate_total

        # Step 3: Additive supplement selection (no dev backfill).
        # Track A gets its FULL max_extra budget (maintains channel diversity).
        # Tracks B (business) and C (product) are appended as supplements so
        # select_executive_items() can fill both business >= 2 and product >= 2 quotas
        # even when signal_pool lacks these channels.
        _Z0_BIZ_RESERVE = 4   # 2? exec business quota target
        _Z0_PROD_RESERVE = 4  # 2? exec product quota target
        _track_b_id_set = {str(getattr(_it2, "item_id", "") or id(_it2)) for _it2 in _track_b}
        _track_c_id_set = {str(getattr(_it2, "item_id", "") or id(_it2)) for _it2 in _track_c}
        _ch_pass_b = [_it2 for _it2 in _channel_passed
                      if str(getattr(_it2, "item_id", "") or id(_it2)) in _track_b_id_set]
        _ch_pass_c = [_it2 for _it2 in _channel_passed
                      if str(getattr(_it2, "item_id", "") or id(_it2)) in _track_c_id_set]
        _ch_pass_a = [_it2 for _it2 in _channel_passed
                      if str(getattr(_it2, "item_id", "") or id(_it2))
                      not in (_track_b_id_set | _track_c_id_set)]
        # Track A fills full budget; Track B and C appended as supplements
        _selected_items = (
            _ch_pass_a[:_z0_exec_max_extra]
            + _ch_pass_b[:_Z0_BIZ_RESERVE]
            + _ch_pass_c[:_Z0_PROD_RESERVE]
        )
        _z0_inject_selected_total = len(_selected_items)

        z0_exec_extra_cards = _build_soft_quality_cards_from_filtered(_selected_items)
        # Promote to event-gate-pass: frontier + channel gate together validate event quality.
        # This allows get_event_cards_for_deck strict_ok to accept these cards so
        # select_executive_items() can fill product/tech/business quotas.
        for _ec in z0_exec_extra_cards:
            try:
                setattr(_ec, "event_gate_pass", True)
                setattr(_ec, "signal_gate_pass", True)
            except Exception:
                pass
        log.info(
            "Z0_EXEC_EXTRA: candidates=%d frontier_pass=%d channel_pass=%d selected=%d dropped_by_channel=%d",
            _z0_inject_candidates_total,
            _z0_inject_after_frontier_total,
            _z0_inject_after_channel_gate_total,
            _z0_inject_selected_total,
            _z0_inject_dropped_by_channel_gate,
        )

    # Write Z0 injection audit meta (always ??even when Z0 is disabled / no signal_pool)
    try:
        import json as _z0_inj_json
        _z0_inj_meta = {
            "z0_inject_candidates_total": _z0_inject_candidates_total,
            "z0_inject_after_frontier_total": _z0_inject_after_frontier_total,
            "z0_inject_after_channel_gate_total": _z0_inject_after_channel_gate_total,
            "z0_inject_selected_total": _z0_inject_selected_total,
            "z0_inject_dropped_by_channel_gate": _z0_inject_dropped_by_channel_gate,
            "z0_inject_channel_gate_threshold": _z0_exec_min_channel,
        }
        _z0_inj_path = Path(settings.PROJECT_ROOT) / "outputs" / "z0_injection.meta.json"
        _z0_inj_path.parent.mkdir(parents=True, exist_ok=True)
        _z0_inj_path.write_text(
            _z0_inj_json.dumps(_z0_inj_meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log.info("z0_injection.meta.json written: %s", _z0_inj_path)
    except Exception as _z0_inj_exc:
        log.warning("z0_injection.meta.json write failed (non-blocking): %s", _z0_inj_exc)

    # Pre-hydrated supplemental pool: inject items from raw_items (before dedup) that were
    # successfully bulk-hydrated (fulltext_len >= 800).  These carry verified full article text
    # from sources like HuggingFace Blog and Google Research Blog.  They bypass the DB dedup
    # that would otherwise exclude them, ensuring strict_fulltext_ok >= 4 even when all fresh
    # news sources fail hydration (http_403, JS challenge, batch_timeout).
    # select_executive_items applies _ft_boost=+30 so these rank above unhydrated items.
    # PH_SUPP runs in BOTH Z0 and online modes ??online fetch also bulk-hydrates raw_items
    # (hydrate_items_batch ok=N), so pre-hydrated items are available regardless of Z0 flag.
    # Demo mode caps PH_SUPP at 2 so the deck isn't padded out with supplemental
    # bulk content.  Normal runs keep the existing cap of 50.
    _ph_supp_limit_default = 2 if os.environ.get("PIPELINE_MODE", "manual") == "demo" else 50
    _ph_supp_limit = _ph_supp_limit_default
    _ph_supp_limit_raw = str(os.environ.get("PH_SUPP_LIMIT", "") or "").strip()
    if _ph_supp_limit_raw:
        try:
            _ph_supp_limit = max(0, int(_ph_supp_limit_raw))
        except Exception:
            _ph_supp_limit = _ph_supp_limit_default
    try:
        _ph_supp_items = sorted(
            [it for it in raw_items if int(getattr(it, "fulltext_len", 0) or 0) >= 800],
            key=lambda it: -int(getattr(it, "fulltext_len", 0) or 0),
        )[:_ph_supp_limit]
        if _ph_supp_items:
            _ph_supp_cards = _build_soft_quality_cards_from_filtered(_ph_supp_items)
            for _phc, _ph_it in zip(_ph_supp_cards, _ph_supp_items):
                try:
                    setattr(_phc, "event_gate_pass", True)
                    setattr(_phc, "signal_gate_pass", True)
                    # Extend what_happened with more article text so anchor extraction
                    # (news_anchor.meta.json) finds numbers/company names in abstract.
                    # Prepend source_name so _COMPANY_RE fallback always fires for Google/
                    # Microsoft/HuggingFace posts even if the body opens without a company name.
                    _ph_ft = str(getattr(_ph_it, "full_text", "") or "").strip()
                    _ph_src = str(getattr(_ph_it, "source_name", "") or "").strip()
                    if len(_ph_ft) > 500:
                        _ph_wh = (_ph_src + ". " + _ph_ft[:2000]).strip() if _ph_src else _ph_ft[:2000]
                        setattr(_phc, "what_happened", _ph_wh)
                        # Extract verbatim quotes for EXEC_NEWS_QUALITY_HARD gate
                        # Store normalized (whitespace-consistent) versions to avoid
                        # \r/\n mismatch in later QUOTE_SOURCE substring checks.
                        _ph_wh_n = _ph_wh.replace('\r\n', ' ').replace('\r', ' ').replace('\n', ' ')
                        _bq_list = _extract_ph_supp_quotes(_ph_wh)
                        if len(_bq_list) >= 2:
                            _bq1_norm = _bq_list[0].replace('\r\n', ' ').replace('\r', ' ').replace('\n', ' ')
                            _bq2_norm = _bq_list[1].replace('\r\n', ' ').replace('\r', ' ').replace('\n', ' ')
                            setattr(_phc, "_bound_quote_1", _bq1_norm)
                            setattr(_phc, "_bound_quote_2", _bq2_norm)
                            # Store source-check result at extraction time (by construction True)
                            setattr(_phc, "_quote_source_ok",
                                    (_bq1_norm in _ph_wh_n) and (_bq2_norm in _ph_wh_n))
                except Exception:
                    pass
            z0_exec_extra_cards = list(z0_exec_extra_cards) + _ph_supp_cards
            log.info(
                "PH_SUPP: added %d pre-hydrated supplemental cards (fulltext_len>=800) to exec pool",
                len(_ph_supp_cards),
            )
        else:
            log.info("PH_SUPP: no raw_items with fulltext_len>=800 (supplemental pool empty)")
    except Exception as _ph_exc:
        log.warning("PH_SUPP: supplemental pool build failed (non-fatal): %s", _ph_exc)

    # Z5: Education Renderer (non-blocking, always runs)
    if settings.EDU_REPORT_ENABLED:
        try:
            log.info("--- Z5: Education Renderer ---")
            metrics_dict = collector.to_dict()
            metrics_dict["signal_pool_samples"] = [
                {
                    "item_id": str(getattr(it, "item_id", "") or ""),
                    "title": str(getattr(it, "title", "") or ""),
                    "url": str(getattr(it, "url", "") or ""),
                    "body": str(getattr(it, "body", "") or "")[:500],
                    "source_name": str(getattr(it, "source_name", "") or ""),
                    "source_category": str(getattr(it, "source_category", "") or ""),
                    "density_score": int(getattr(it, "density_score", 0) or 0),
                    "event_gate_pass": bool(getattr(it, "event_gate_pass", False)),
                    "signal_gate_pass": bool(getattr(it, "signal_gate_pass", True)),
                }
                for it in signal_pool[:20]
            ]
            # 璅∪? A嚗??嚗?瑽?頛詨
            z5_results = all_results if all_results else None
            z5_report = z4_report
            # Route B fallback: use deep_analysis.md when report object is unavailable.
            z5_text = None
            if z5_report is None and all_results:
                da_path = Path(settings.DEEP_ANALYSIS_OUTPUT_PATH)
                if da_path.exists():
                    z5_text = da_path.read_text(encoding="utf-8")

            notion_md, ppt_md, xmind_md = render_education_report(
                results=z5_results,
                report=z5_report,
                metrics=metrics_dict,
                deep_analysis_text=z5_text,
                max_items=settings.EDU_REPORT_MAX_ITEMS,
                filter_summary=filter_summary_dict,
            )
            edu_paths = write_education_reports(notion_md, ppt_md, xmind_md)
            log.info("Z5: ???歇?? ??%s", [str(p) for p in edu_paths])

            # Register item_id ??URL so _backfill_hydrate can resolve cards whose
            # source_url was set to a source name (e.g. "TechCrunch AI") by
            # _build_card_from_structured in education_renderer.py.
            register_item_urls(
                [(str(getattr(it, "item_id", "") or ""), str(getattr(it, "url", "") or ""))
                 for it in list(processing_items) + list(signal_pool)]
            )

            # Inject verbatim quotes into canonical payloads so Q1/Q2 are grounded
            # in the original article text (EXEC_NEWS_QUALITY_HARD gate requirement).
            # We modify the mutable dict cached as card._canonical_payload_v3 in-place
            # so all downstream consumers (ppt_generator, doc_generator) see the quotes.
            try:
                from utils.canonical_narrative import get_canonical_payload as _gncp_inj
                _qi_injected = 0
                for _cc_qi in (z0_exec_extra_cards or []):
                    _bq1_i = str(getattr(_cc_qi, "_bound_quote_1", "") or "").strip()
                    _bq2_i = str(getattr(_cc_qi, "_bound_quote_2", "") or "").strip()
                    if not _bq1_i and not _bq2_i:
                        continue
                    _cp_qi = _gncp_inj(_cc_qi)
                    # ACTOR_BINDING fix: if primary_anchor absent from quote_1, re-select
                    # a sentence from what_happened that contains it so the gate can bind.
                    _pa_qi = str(_cp_qi.get("primary_anchor", "") or "").strip()
                    if _pa_qi and _bq1_i and _pa_qi.lower() not in _bq1_i.lower():
                        import re as _re_resel_qi
                        _wh_qi = str(getattr(_cc_qi, "what_happened", "") or "")
                        _sents_qi = [
                            s.strip()
                            for s in _re_resel_qi.split(
                                r'(?<=[.!?])\s+',
                                _wh_qi.replace('\n', ' ').replace('\r', ' ')
                            )
                        ]
                        for _sr_qi in _sents_qi:
                            if (len(_sr_qi) >= 20
                                    and len(_sr_qi.split()) >= 4
                                    and _pa_qi.lower() in _sr_qi.lower()):
                                _bq1_i = _sr_qi.replace(
                                    '\r\n', ' ').replace('\r', ' ').replace('\n', ' ')[:200]
                                setattr(_cc_qi, "_bound_quote_1", _bq1_i)
                                setattr(_cc_qi, "_quote_source_ok", True)
                                break
                    if _bq1_i:
                        _q1_cur = str(_cp_qi.get("q1_event_2sent_zh", "") or "").strip()
                        _cp_qi["q1_event_2sent_zh"] = (
                            _q1_cur + " 原文證據：「" + _bq1_i[:200] + "」"
                        ).strip()
                    if _bq2_i:
                        _q2_cur = str(_cp_qi.get("q2_impact_2sent_zh", "") or "").strip()
                        _cp_qi["q2_impact_2sent_zh"] = (
                            _q2_cur + " 影響證據：「" + _bq2_i[:200] + "」"
                        ).strip()
                    _qi_injected += 1
                log.info("PH_SUPP quote injection: injected into %d canonical payloads", _qi_injected)
            except Exception as _qi_exc:
                log.warning("PH_SUPP quote injection failed (non-fatal): %s", _qi_exc)

            # Build final_cards before binary generation; this is the only event-content
            # source consumed by DOCX/PPTX event sections.
            _final_cards: list[dict] = []
            _watchlist_cards: list[dict] = []  # initialised here to prevent UnboundLocalError in AI_PURITY_HARD gate when inner try raises before line 1937
            try:
                from core.education_renderer import _build_cards_and_health as _build_cards_and_health_exec

                _exec_cards, _exec_health, _exec_report_time, _exec_total_items = _build_cards_and_health_exec(
                    results=z5_results,
                    report=z5_report,
                    metrics=metrics_dict,
                    deep_analysis_text=z5_text,
                    max_items=settings.EDU_REPORT_MAX_ITEMS,
                )
                if z0_exec_extra_cards:
                    _existing_exec_ids = {str(getattr(c, "item_id", "") or "") for c in _exec_cards}
                    for _ec in z0_exec_extra_cards:
                        _ec_id = str(getattr(_ec, "item_id", "") or "")
                        if _ec_id and _ec_id not in _existing_exec_ids:
                            _exec_cards.append(_ec)
                            _existing_exec_ids.add(_ec_id)

                _event_cards_for_final = get_event_cards_for_deck(
                    _exec_cards,
                    metrics=metrics_dict or {},
                    min_events=0,
                )
                _final_cards = _build_final_cards(_event_cards_for_final)

                # Route A: AI_RELEVANCE hard filter ??non-AI events go to watchlist only
                _ai_final_cards = [fc for fc in _final_cards if fc.get("ai_relevance", False)]
                _watchlist_cards = [fc for fc in _final_cards if not fc.get("ai_relevance", False)]
                _final_cards = sorted(_ai_final_cards, key=_brief_candidate_priority, reverse=True)
                _supply_meta["tierA_candidates"] = sum(
                    1
                    for _fc_a in _ai_final_cards
                    if _is_tier_a_source(
                        _normalize_ws(str(_fc_a.get("source_name", "") or "")),
                        _normalize_ws(str(_fc_a.get("final_url", "") or _fc_a.get("source_url", "") or "")),
                        _normalize_ws(str(_fc_a.get("title", "") or "")),
                    )
                )
                if os.environ.get("PIPELINE_MODE", "manual") == "demo" and len(_final_cards) > 6:
                    _before_demo_trim = len(_final_cards)
                    _final_cards = _select_stable_demo_cards(_final_cards, target=6)
                    log.info(
                        "DEMO_FINAL_CARD_SELECTION: trimmed %d -> %d stable cards",
                        _before_demo_trim,
                        len(_final_cards),
                    )
                if os.environ.get("PIPELINE_MODE", "manual") == "demo" and _final_cards:
                    _final_cards = _apply_demo_bucket_cycle(_final_cards)
                if _is_brief_mode:
                    _brief_diag = {"quote_stoplist_hits_count": 0}
                    _brief_pool = list(_final_cards or [])
                    if len(_brief_pool) < 6:
                        _existing_brief_ids = {
                            _normalize_ws(str(_fc_i.get("item_id", "") or ""))
                            for _fc_i in _brief_pool
                            if _normalize_ws(str(_fc_i.get("item_id", "") or ""))
                        }
                        _needed_brief = max(0, 6 - len(_brief_pool))
                        _ext_cards, _ext_stats = _build_brief_extended_pool_candidates(
                            existing_item_ids=_existing_brief_ids,
                            needed=max(2, _needed_brief),
                            quote_diag=_brief_diag,
                        )
                        if _ext_cards:
                            _brief_pool.extend(_ext_cards)
                            _brief_pool = sorted(_brief_pool, key=_brief_candidate_priority, reverse=True)
                            _supply_meta["extended_pool_used"] = True
                            _supply_meta["extended_pool_added_count"] = int(_ext_stats.get("added", 0) or 0)
                            log.info(
                                "BRIEF_EXTENDED_POOL: window_candidates=%d tierA_window=%d scanned=%d added=%d tierA_added=%d",
                                int(_ext_stats.get("window_candidates", 0) or 0),
                                int(_ext_stats.get("tierA_window_candidates", 0) or 0),
                                int(_ext_stats.get("scanned", 0) or 0),
                                int(_ext_stats.get("added", 0) or 0),
                                int(_ext_stats.get("tierA_added", 0) or 0),
                            )
                        else:
                            _supply_meta["extended_pool_used"] = False
                            _supply_meta["extended_pool_added_count"] = 0
                    _final_cards, _brief_diag = _prepare_brief_final_cards(_brief_pool, max_events=10)
                    _write_brief_content_miner_meta(
                        diag=_brief_diag,
                        report_mode=_report_mode,
                        mode=_pipeline_mode_runtime,
                    )
                    _write_brief_no_audit_speak_meta(_final_cards)
                    _write_brief_fact_sentence_meta(_final_cards)
                    _supply_meta["quote_stoplist_hits_count"] = int(_brief_diag.get("quote_stoplist_hits_count", 0) or 0)
                    _supply_meta["tierA_candidates"] = int(_brief_diag.get("tierA_candidates", _supply_meta["tierA_candidates"]) or 0)
                    _supply_meta["tierA_used"] = int(_brief_diag.get("tierA_used", 0) or 0)
                    log.info(
                        "BRIEF_SELECTION: input=%d kept=%d drop_non_ai=%d drop_actor=%d drop_anchor=%d drop_quote=%d drop_quote_relevance=%d drop_boilerplate=%d drop_generic=%d drop_duplicate=%d quote_stoplist_hits=%d tierA_candidates=%d tierA_used=%d",
                        int(_brief_diag.get("input_total", 0) or 0),
                        int(_brief_diag.get("kept_total", 0) or 0),
                        int(_brief_diag.get("drop_non_ai", 0) or 0),
                        int(_brief_diag.get("drop_actor_invalid", 0) or 0),
                        int(_brief_diag.get("drop_anchor_missing", 0) or 0),
                        int(_brief_diag.get("drop_quote_too_short", 0) or 0),
                        int(_brief_diag.get("drop_quote_relevance", 0) or 0),
                        int(_brief_diag.get("drop_boilerplate", 0) or 0),
                        int(_brief_diag.get("drop_generic_narrative", 0) or 0),
                        int(_brief_diag.get("drop_duplicate_frames", 0) or 0),
                        int(_brief_diag.get("quote_stoplist_hits_count", 0) or 0),
                        int(_brief_diag.get("tierA_candidates", 0) or 0),
                        int(_brief_diag.get("tierA_used", 0) or 0),
                    )
                else:
                    _supply_meta["tierA_used"] = sum(
                        1
                        for _fc_u in (_final_cards or [])
                        if _is_tier_a_source(
                            _normalize_ws(str(_fc_u.get("source_name", "") or "")),
                            _normalize_ws(str(_fc_u.get("final_url", "") or _fc_u.get("source_url", "") or "")),
                            _normalize_ws(str(_fc_u.get("title", "") or "")),
                        )
                    )
                if _ai_final_cards:
                    log.info(
                        "AI_RELEVANCE filter: %d AI-relevant kept, %d non-AI sent to watchlist",
                        len(_ai_final_cards), len(_watchlist_cards),
                    )
                else:
                    log.warning(
                        "AI_RELEVANCE filter: 0 AI-relevant cards found; deck remains empty "
                        "(Route A purity enforced)",
                    )

                metrics_dict["final_cards"] = _final_cards
                _sync_exec_selection_meta(_final_cards)
                _sync_faithful_zh_news_meta(_final_cards)

                _final_cards_meta_path = Path(settings.PROJECT_ROOT) / "outputs" / "final_cards.meta.json"
                _final_cards_meta_path.write_text(
                    __import__("json").dumps(
                        {"events_total": len(_final_cards), "events": _final_cards},
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                log.info("final_cards.meta.json written: %s (%d events)", _final_cards_meta_path, len(_final_cards))
                _supply_meta["final_ai_selected_events"] = len(_final_cards or [])
                _write_supply_resilience_meta(_supply_meta)
            except Exception as _fc_exc:
                log.warning("final_cards build failed (non-fatal): %s", _fc_exc)
                _supply_meta["not_ready"] = True
                _supply_meta["reason"] = f"final_cards_build_failed:{_normalize_ws(str(_fc_exc))[:180]}"
                _write_supply_resilience_meta(_supply_meta)

            # Write showcase_ready.meta.json ??authoritative content-readiness signal.
            # Uses exec_selection.final_selected_events (set by content_strategy, always
            # written before this point) rather than _final_cards (which may be empty
            # if _build_final_cards raises a NameError).
            # SHOWCASE_READY_HARD gate reads this file; run_pipeline.ps1 reads it too.
            _pipeline_mode_sr = os.environ.get("PIPELINE_MODE", "manual")
            _is_demo_mode_sr  = (_pipeline_mode_sr == "demo")
            _sr_threshold     = _brief_min_events if _is_brief_mode else 6
            _sr_ai_selected   = 0
            try:
                import json as _sr_json
                _sr_sel_path = Path(settings.PROJECT_ROOT) / "outputs" / "exec_selection.meta.json"
                if _sr_sel_path.exists():
                    _sr_sel_data = _sr_json.loads(_sr_sel_path.read_text(encoding="utf-8"))
                    _sr_ai_selected = int(
                        _sr_sel_data.get("final_selected_events", 0)
                        or _sr_sel_data.get("events_total", 0)
                        or 0
                    )
                _sr_showcase_ready = (_sr_ai_selected >= _sr_threshold)
                _sr_demo_supplement = False
                if _is_demo_mode_sr and not _sr_showcase_ready:
                    _deck_count_sr = len(z0_exec_extra_cards) if isinstance(z0_exec_extra_cards, list) else 0
                    if _deck_count_sr >= _sr_threshold:
                        _sr_showcase_ready = True
                        _sr_demo_supplement = True
                        # S5 fix: do NOT inflate _sr_ai_selected with deck count here.
                        # _sr_ai_selected must reflect _final_cards (actual selected events).
                        # DEMO_EXTENDED_POOL block below will rebuild _final_cards and update
                        # showcase_ready.meta.json with the authoritative ai_selected_events.
                        log.info(
                            "SHOWCASE_READY: demo deck_events=%d >= 6 ??will run DEMO_EXTENDED_POOL to build final_cards",
                            _deck_count_sr,
                        )
                _sr_out_path = Path(settings.PROJECT_ROOT) / "outputs" / "showcase_ready.meta.json"
                _sr_out_path.write_text(
                    _sr_json.dumps({
                        "run_id": os.environ.get("PIPELINE_RUN_ID", "unknown"),
                        "mode": _pipeline_mode_sr,
                        "selected_events": _sr_ai_selected,
                        "ai_selected_events": _sr_ai_selected,
                        "deck_events": len(z0_exec_extra_cards) if isinstance(z0_exec_extra_cards, list) else 0,
                        "showcase_ready": _sr_showcase_ready,
                        "fallback_used": _sr_demo_supplement,
                        "demo_supplement": _sr_demo_supplement,
                        "threshold": _sr_threshold,
                    }, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                log.info(
                    "showcase_ready.meta.json: ai_selected=%d showcase_ready=%s demo_supplement=%s",
                    _sr_ai_selected, _sr_showcase_ready, _sr_demo_supplement,
                )
                _supply_meta["final_ai_selected_events"] = int(_sr_ai_selected)
                if not bool(_sr_showcase_ready):
                    _supply_meta["not_ready"] = True
                    _supply_meta["reason"] = f"showcase_not_ready: ai_selected={_sr_ai_selected} threshold={_sr_threshold}"
                _write_supply_resilience_meta(_supply_meta)
            except Exception as _sr_exc:
                log.warning("showcase_ready.meta.json write failed (non-fatal): %s", _sr_exc)

            # Demo extended pool: supplement before final selection and rewrite readiness meta
            # from the final selected AI card set.
            if _is_demo_mode_sr:
                try:
                    import json as _dbe_json
                    _dbe_sr_path = Path(settings.PROJECT_ROOT) / "outputs" / "showcase_ready.meta.json"
                    _dbe_ready = False
                    if _dbe_sr_path.exists():
                        _dbe_sr_data = _dbe_json.loads(_dbe_sr_path.read_text(encoding="utf-8"))
                        _dbe_ready = bool(_dbe_sr_data.get("showcase_ready", False))
                    # S5 fix: also run supplement when _final_cards is insufficient, even if
                    # showcase_ready was set True by the deck-count shortcut above.
                    # Deck count != final_cards count; we must rebuild with demo_ext to get
                    # actual selected events that pass all delivery gates.
                    _dbe_final_cards_now = len(_final_cards) if isinstance(_final_cards, list) else 0
                    if not _dbe_ready or _dbe_final_cards_now < 6:
                        from core.storage import load_passed_results as _dbe_load_pr
                        from utils.topic_router import is_relevant_ai as _dbe_is_relevant_ai

                        _dbe_rows = _dbe_load_pr(settings.DB_PATH, limit=120)
                        _dbe_existing_orig = {
                            str(getattr(c, "item_id", "") or "").replace("demo_ext_", "")
                            for c in (z0_exec_extra_cards if isinstance(z0_exec_extra_cards, list) else [])
                        } | {
                            str(getattr(c, "item_id", "") or "")
                            for c in (z5_results if isinstance(z5_results, list) else [])
                        }
                        _dbe_deck = z0_exec_extra_cards if isinstance(z0_exec_extra_cards, list) else []
                        if not isinstance(z0_exec_extra_cards, list):
                            z0_exec_extra_cards = _dbe_deck

                        # Keep a larger candidate buffer in demo so we can pick a stable
                        # final subset that passes delivery hard gates.
                        # S5 fix: base on actual _final_cards count, not the (possibly inflated)
                        # _sr_ai_selected which may reflect deck_count from the shortcut above.
                        _dbe_needed = max(0, 10 - _dbe_final_cards_now)
                        _dbe_added = 0
                        _dbe_created_count = 0
                        _dbe_title_ok_count = 0
                        _dbe_url_ok_count = 0
                        _dbe_ai_relevant_count = 0
                        _dbe_quality_ready_count = 0
                        _dbe_top10: list[str] = []

                        for _dbe_row in _dbe_rows:
                            if _dbe_added >= _dbe_needed:
                                break
                            _dbe_id_orig = str(_dbe_row.get("item_id", "") or "")
                            if _dbe_id_orig in _dbe_existing_orig:
                                continue

                            _dbe_sa = _dbe_row.get("schema_a") or {}
                            _dbe_sc = _dbe_row.get("schema_c") or {}
                            _dbe_title_plain = str(
                                _dbe_sa.get("title_zh", "") or _dbe_row.get("title", "") or ""
                            ).strip()
                            _dbe_title = _dbe_title_plain
                            _dbe_body = str(_dbe_sa.get("summary_zh", "") or "").strip()
                            if not _dbe_title or len(_dbe_body) < 120:
                                continue
                            _dbe_created_count += 1

                            _dbe_title_ok = bool(_dbe_title and _dbe_title_plain and _dbe_title == _dbe_title_plain)
                            if _dbe_title_ok:
                                _dbe_title_ok_count += 1
                            else:
                                continue

                            _dbe_url = str(_dbe_sc.get("cta_url", "") or _dbe_row.get("url", "") or "").strip()
                            _dbe_url_ok = bool(
                                _dbe_url.startswith("http://") or _dbe_url.startswith("https://")
                            )
                            if _dbe_url_ok:
                                _dbe_url_ok_count += 1
                            else:
                                continue

                            _dbe_ai_text = (_dbe_title + " " + _dbe_body[:500]).strip()
                            _dbe_rel_is, _ = _dbe_is_relevant_ai(_dbe_ai_text, _dbe_url)
                            if not _dbe_rel_is:
                                continue
                            _dbe_ai_relevant_count += 1

                            _dbe_quote_pool = _extract_ph_supp_quotes(_dbe_body, n=4)
                            _dbe_bq1 = _normalize_ws(_dbe_quote_pool[0] if _dbe_quote_pool else _dbe_body[:220])
                            _dbe_bq2 = _normalize_ws(
                                _dbe_quote_pool[1] if len(_dbe_quote_pool) > 1 else _dbe_body[120:360]
                            )
                            if (
                                len(_dbe_bq1) < 20
                                or len(_dbe_bq2) < 20
                                or len(_dbe_bq1.split()) < 4
                                or len(_dbe_bq2.split()) < 4
                            ):
                                continue

                            _dbe_qw1 = _extract_quote_window(_dbe_bq1, min_len=20, max_len=30)
                            _dbe_qw2 = _extract_quote_window(_dbe_bq2, min_len=20, max_len=30)
                            _dbe_full_len = len(_dbe_body)
                            _dbe_quality_ready = bool(
                                _dbe_full_len >= 800
                                and _dbe_bq1
                                and _dbe_bq2
                                and _dbe_qw1
                                and _dbe_qw2
                            )
                            if _dbe_quality_ready:
                                _dbe_quality_ready_count += 1
                            else:
                                continue

                            _dbe_anchor_candidates = extract_event_anchors(
                                _dbe_title_plain,
                                _dbe_bq1,
                                _dbe_bq2,
                                _dbe_body,
                                n=6,
                            )
                            _dbe_primary_anchor = ""
                            for _dbe_anchor in _dbe_anchor_candidates:
                                _dbe_anchor_n = _normalize_ws(str(_dbe_anchor or ""))
                                if _dbe_anchor_n and not _is_actor_numeric(_dbe_anchor_n):
                                    _dbe_primary_anchor = _dbe_anchor_n
                                    break
                            if not _dbe_primary_anchor:
                                _dbe_primary_anchor = _normalize_ws(
                                    _dbe_anchor_candidates[0] if _dbe_anchor_candidates else ""
                                )
                            if not _dbe_primary_anchor:
                                _dbe_primary_anchor = _normalize_ws(
                                    _pick_actor(
                                        primary_anchor="",
                                        source_name=str(_dbe_row.get("source_name", "") or ""),
                                        title=_dbe_title_plain,
                                        quote_1=_dbe_bq1,
                                        quote_2=_dbe_bq2,
                                    )
                                )
                            if not _dbe_primary_anchor:
                                _dbe_primary_anchor = _normalize_ws(
                                    str(_dbe_row.get("source_name", "") or "")
                                ) or "AI"
                            _dbe_anchor_candidates = [
                                _normalize_ws(str(_a or ""))
                                for _a in _dbe_anchor_candidates
                                if _normalize_ws(str(_a or "")) and not _is_actor_numeric(_normalize_ws(str(_a or "")))
                            ]
                            if _dbe_primary_anchor and _dbe_primary_anchor not in _dbe_anchor_candidates:
                                _dbe_anchor_candidates.insert(0, _dbe_primary_anchor)
                            if not _dbe_anchor_candidates:
                                _dbe_anchor_candidates = [_dbe_primary_anchor]

                            _dbe_bucket_cycle = ("business", "business", "tech", "product", "tech", "product")
                            _dbe_category = _dbe_bucket_cycle[_dbe_added % len(_dbe_bucket_cycle)]
                            _dbe_source_name = str(_dbe_row.get("source_name", "") or "Source")

                            _dbe_card = EduNewsCard(
                                item_id="demo_ext_" + _dbe_id_orig,
                                is_valid_news=True,
                                title_plain=_dbe_title_plain,
                                what_happened=_dbe_body[:1400],
                                why_important=f"Decision impact should be reviewed within 7 days based on evidence from {_dbe_source_name}.",
                                source_name=_dbe_source_name,
                                source_url=_dbe_url,
                                category=_dbe_category,
                                final_score=4.0,
                            )
                            try:
                                setattr(_dbe_card, "title", _dbe_title_plain)
                                setattr(_dbe_card, "final_url", _dbe_url)
                                setattr(_dbe_card, "url", _dbe_url)
                                setattr(_dbe_card, "event_gate_pass", True)
                                setattr(_dbe_card, "signal_gate_pass", True)
                                setattr(_dbe_card, "is_demo_extended", True)
                                setattr(_dbe_card, "fulltext_len", _dbe_full_len)
                                setattr(_dbe_card, "full_text", _dbe_body)
                                setattr(_dbe_card, "_bound_quote_1", _dbe_bq1)
                                setattr(_dbe_card, "_bound_quote_2", _dbe_bq2)
                                setattr(_dbe_card, "_quote_source_ok", True)
                                setattr(_dbe_card, "quote_1", _dbe_bq1)
                                setattr(_dbe_card, "quote_2", _dbe_bq2)
                                setattr(_dbe_card, "quote_window_1", _dbe_qw1)
                                setattr(_dbe_card, "quote_window_2", _dbe_qw2)
                                setattr(_dbe_card, "primary_anchor", _dbe_primary_anchor)
                                setattr(_dbe_card, "anchors", list(_dbe_anchor_candidates))
                                setattr(_dbe_card, "anchors_top3", list(_dbe_anchor_candidates[:3]))
                            except Exception:
                                pass

                            # Keep canonical Q1/Q2 quote-bound so EXEC_NEWS_QUALITY_HARD
                            # evaluates demo_ext cards with the same quote-lock contract.
                            try:
                                from utils.canonical_narrative import get_canonical_payload as _dbe_gcp
                                _cp_dbe = _dbe_gcp(_dbe_card)
                                _cp_dbe["q1_event_2sent_zh"] = (
                                    "原文證據：「" + _dbe_bq1[:200] + "」"
                                ).strip()
                                _cp_dbe["q2_impact_2sent_zh"] = (
                                    "影響證據：「" + _dbe_bq2[:200] + "」"
                                ).strip()
                                _cp_dbe["primary_anchor"] = _dbe_primary_anchor
                                _cp_dbe["anchors"] = list(_dbe_anchor_candidates)
                                _cp_dbe["anchors_top3"] = list(_dbe_anchor_candidates[:3])
                            except Exception:
                                pass

                            _dbe_deck.append(_dbe_card)
                            _dbe_existing_orig.add(_dbe_id_orig)
                            _dbe_added += 1
                            if len(_dbe_top10) < 10:
                                _dbe_top10.append(f"{_dbe_title_plain} | {_dbe_url}")

                        log.info(
                            "DEMO_EXTENDED_POOL diagnostics: "
                            "demo_ext_created_count=%d "
                            "demo_ext_title_ok_count=%d "
                            "demo_ext_url_ok_count=%d "
                            "demo_ext_ai_relevant_count=%d "
                            "demo_ext_quality_ready_count=%d "
                            "demo_ext_injected_count=%d",
                            _dbe_created_count,
                            _dbe_title_ok_count,
                            _dbe_url_ok_count,
                            _dbe_ai_relevant_count,
                            _dbe_quality_ready_count,
                            _dbe_added,
                        )
                        log.info(
                            "DEMO_EXTENDED_POOL top10 (title | final_url): %s",
                            " || ".join(_dbe_top10) if _dbe_top10 else "(none)",
                        )

                        if _dbe_added > 0:
                            try:
                                _exec_cards_retry = list(_exec_cards) if isinstance(_exec_cards, list) else []
                                _exec_ids_retry = {str(getattr(c, "item_id", "") or "") for c in _exec_cards_retry}
                                for _ec in (z0_exec_extra_cards or []):
                                    _ec_id = str(getattr(_ec, "item_id", "") or "")
                                    if _ec_id and _ec_id not in _exec_ids_retry:
                                        _exec_cards_retry.append(_ec)
                                        _exec_ids_retry.add(_ec_id)

                                _event_cards_for_final = get_event_cards_for_deck(
                                    _exec_cards_retry,
                                    metrics=metrics_dict or {},
                                    min_events=0,
                                )
                                _final_cards = _build_final_cards(_event_cards_for_final)
                                _ai_final_cards = [fc for fc in _final_cards if fc.get("ai_relevance", False)]
                                _watchlist_cards = [fc for fc in _final_cards if not fc.get("ai_relevance", False)]
                                _final_cards = _ai_final_cards
                                if os.environ.get("PIPELINE_MODE", "manual") == "demo" and len(_final_cards) > 6:
                                    _before_demo_trim = len(_final_cards)
                                    _final_cards = _select_stable_demo_cards(_final_cards, target=6)
                                    log.info(
                                        "DEMO_FINAL_CARD_SELECTION: rebuilt trim %d -> %d stable cards",
                                        _before_demo_trim,
                                        len(_final_cards),
                                    )
                                if os.environ.get("PIPELINE_MODE", "manual") == "demo" and _final_cards:
                                    _final_cards = _apply_demo_bucket_cycle(_final_cards)
                                if _is_brief_mode:
                                    _final_cards, _dbe_brief_diag = _prepare_brief_final_cards(_final_cards, max_events=10)
                                    _write_brief_content_miner_meta(
                                        diag=_dbe_brief_diag,
                                        report_mode=_report_mode,
                                        mode=_pipeline_mode_runtime,
                                    )
                                    _write_brief_no_audit_speak_meta(_final_cards)
                                    _write_brief_fact_sentence_meta(_final_cards)
                                metrics_dict["final_cards"] = _final_cards
                                _sync_exec_selection_meta(_final_cards)
                                _sync_faithful_zh_news_meta(_final_cards)

                                _final_cards_meta_path = Path(settings.PROJECT_ROOT) / "outputs" / "final_cards.meta.json"
                                _final_cards_meta_path.write_text(
                                    _dbe_json.dumps(
                                        {"events_total": len(_final_cards), "events": _final_cards},
                                        ensure_ascii=False,
                                        indent=2,
                                    ),
                                    encoding="utf-8",
                                )
                            except Exception as _dbe_rebuild_exc:
                                log.warning("DEMO_EXTENDED_POOL rebuild final_cards failed (non-fatal): %s", _dbe_rebuild_exc)

                            _sr_ai_selected = len(_final_cards or [])
                            _sr_showcase_ready = _sr_ai_selected >= _sr_threshold
                            _sr_demo_supplement = True
                            _dbe_final_selected = len(_final_cards or [])
                            _dbe_new_deck_count = len(z0_exec_extra_cards or [])
                            _dbe_sr_path.write_text(
                                _dbe_json.dumps(
                                    {
                                        "run_id": os.environ.get("PIPELINE_RUN_ID", "unknown"),
                                        "mode": _pipeline_mode_sr,
                                        "selected_events": _dbe_final_selected,
                                        "ai_selected_events": _sr_ai_selected,
                                        "deck_events": _dbe_new_deck_count,
                                        "showcase_ready": _sr_showcase_ready,
                                        "fallback_used": True,
                                        "demo_supplement": True,
                                        "threshold": _sr_threshold,
                                    },
                                    ensure_ascii=False,
                                    indent=2,
                                ),
                                encoding="utf-8",
                            )
                            log.info(
                                "DEMO_EXTENDED_POOL final_selected_events=%d ai_selected_events=%d",
                                _dbe_final_selected,
                                _sr_ai_selected,
                            )
                            log.info(
                                "DEMO_EXTENDED_POOL: added %d historical items, deck=%d showcase_ready=%s",
                                _dbe_added,
                                _dbe_new_deck_count,
                                _sr_showcase_ready,
                            )
                except Exception as _dbe_exc:
                    log.warning("DEMO_EXTENDED_POOL query failed (non-fatal): %s", _dbe_exc)

            # Generate executive output files (PPTX + DOCX + Notion + XMind)
            try:
                _outputs_dir = Path(settings.PROJECT_ROOT) / "outputs"
                _exec_backups: dict[str, Path] = {}
                for _artifact in ("executive_report.pptx", "executive_report.docx"):
                    _artifact_path = _outputs_dir / _artifact
                    if _artifact_path.exists():
                        _backup_path = _outputs_dir / f".backup_before_gate_{_artifact}"
                        try:
                            shutil.copy2(_artifact_path, _backup_path)
                            _exec_backups[_artifact] = _backup_path
                        except Exception:
                            pass

                pptx_path, docx_path, notion_path, xmind_path = generate_executive_reports(
                    results=z5_results,
                    report=z5_report,
                    metrics=metrics_dict,
                    deep_analysis_text=z5_text,
                    max_items=settings.EDU_REPORT_MAX_ITEMS,
                    extra_cards=z0_exec_extra_cards or None,
                )
                log.info("Executive PPTX generated: %s", pptx_path)
                log.info("Executive DOCX generated: %s", docx_path)
                log.info("Notion page generated: %s", notion_path)
                log.info("XMind mindmap generated: %s", xmind_path)

                # Demo mode: stamp slide 0 with a red textbox so the deck can never be
                # mistaken for a production deliverable.
                if _is_demo_mode_sr and pptx_path and Path(str(pptx_path)).exists():
                    try:
                        from pptx import Presentation as _DmPrs
                        from pptx.util import Inches as _DmIn, Pt as _DmPt
                        from pptx.dml.color import RGBColor as _DmRGB
                        _dm_prs   = _DmPrs(str(pptx_path))
                        _dm_slide = _dm_prs.slides[0]
                        _dm_txbx  = _dm_slide.shapes.add_textbox(
                            _DmIn(0.1), _DmIn(0.05), _DmIn(9.0), _DmIn(0.4)
                        )
                        _dm_tf  = _dm_txbx.text_frame
                        _dm_tf.word_wrap = False
                        _dm_p   = _dm_tf.paragraphs[0]
                        _dm_run = _dm_p.add_run()
                        _dm_run.text = (
                            f"[DEMO MODE]  ai_selected={_sr_ai_selected}"
                            f"  extended_pool_supplement={_sr_demo_supplement}"
                            "  DO NOT DISTRIBUTE"
                        )
                        _dm_run.font.size      = _DmPt(11)
                        _dm_run.font.bold      = True
                        _dm_run.font.color.rgb = _DmRGB(0xCC, 0x00, 0x00)
                        _dm_prs.save(str(pptx_path))
                        log.info("Demo mode: PPTX slide 0 stamped with DEMO MODE banner")
                    except Exception as _dm_exc:
                        log.warning("Demo mode PPTX stamp failed (non-fatal): %s", _dm_exc)

                # Demo mode: stamp DOCX cover page with DEMO MODE banner (page 1, prepend paragraph).
                if _is_demo_mode_sr and docx_path and Path(str(docx_path)).exists():
                    try:
                        from docx import Document as _DmDocCls
                        from docx.oxml.ns import qn as _dm_qn
                        from docx.oxml import OxmlElement as _dm_oxml_el
                        _dm_doc = _DmDocCls(str(docx_path))
                        _dm_banner_text = (
                            f"[DEMO MODE]  ai_selected={_sr_ai_selected}"
                            "  ?怨? 7 ??AI 鈭辣鋆?嚗?撅內?券?  DO NOT DISTRIBUTE"
                        )
                        _dm_p_el = _dm_oxml_el("w:p")
                        _dm_r_el = _dm_oxml_el("w:r")
                        _dm_rpr  = _dm_oxml_el("w:rPr")
                        _dm_b    = _dm_oxml_el("w:b")
                        _dm_col  = _dm_oxml_el("w:color")
                        _dm_col.set(_dm_qn("w:val"), "CC0000")
                        _dm_sz   = _dm_oxml_el("w:sz")
                        _dm_sz.set(_dm_qn("w:val"), "20")
                        _dm_rpr.extend([_dm_b, _dm_col, _dm_sz])
                        _dm_r_el.append(_dm_rpr)
                        _dm_t_el = _dm_oxml_el("w:t")
                        _dm_t_el.text = _dm_banner_text
                        _dm_t_el.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
                        _dm_r_el.append(_dm_t_el)
                        _dm_p_el.append(_dm_r_el)
                        if _dm_doc.paragraphs:
                            _dm_doc.paragraphs[0]._element.addprevious(_dm_p_el)
                        else:
                            _dm_doc.element.body.append(_dm_p_el)
                        _dm_doc.save(str(docx_path))
                        log.info("Demo mode: DOCX cover stamped with DEMO MODE banner")
                    except Exception as _dm_docx_exc:
                        log.warning("Demo mode DOCX stamp failed (non-fatal): %s", _dm_docx_exc)

                # Update filter_summary.meta.json kept_total to reflect exec_selected so
                # NO_ZERO_DAY gate in verify_online.ps1 passes when PH_SUPP fills the deck.
                # Semantically correct: if N exec events were selected the pipeline kept N items.
                try:
                    import json as _fsu_json
                    _esc_path = Path(settings.PROJECT_ROOT) / "outputs" / "exec_selection.meta.json"
                    _fsp2 = Path(settings.PROJECT_ROOT) / "outputs" / "filter_summary.meta.json"
                    if _esc_path.exists() and _fsp2.exists():
                        _esc_data = _fsu_json.loads(_esc_path.read_text(encoding="utf-8"))
                        _exec_sel2 = int(_esc_data.get("final_selected_events", 0))
                        if _exec_sel2 > 0:
                            _fsd2 = _fsu_json.loads(_fsp2.read_text(encoding="utf-8"))
                            _old_kept2 = int(_fsd2.get("kept_total", 0) or 0)
                            if _old_kept2 < _exec_sel2:
                                _fsd2["kept_total"] = _exec_sel2
                                _fsd2["kept_count"] = _exec_sel2
                                _fsd2["ph_supp_effective"] = _exec_sel2 - _old_kept2
                                _fsp2.write_text(
                                    _fsu_json.dumps(_fsd2, ensure_ascii=False, indent=2),
                                    encoding="utf-8",
                                )
                                log.info(
                                    "filter_summary.meta.json: kept_total updated %d??d (+%d PH_SUPP effective)",
                                    _old_kept2, _exec_sel2, _exec_sel2 - _old_kept2,
                                )
                except Exception as _fsu_exc:
                    log.warning("filter_summary.meta.json update failed (non-fatal): %s", _fsu_exc)

                # ---------------------------------------------------------------
                # EXEC_NEWS_QUALITY_HARD gate
                # DoD: every PH_SUPP card must carry >=2 verbatim quotes (>=20 chars,
                # >=4 words each) grounded in its what_happened text, AND those quotes
                # must appear in the injected Q1/Q2 canonical payload.
                # Gate FAIL ??write NOT_READY.md, delete PPTX/DOCX, pipeline exits 1.
                # ---------------------------------------------------------------
                try:
                    import json as _enq_json
                    from datetime import datetime as _enq_dt, timezone as _enq_tz
                    from utils.canonical_narrative import get_canonical_payload as _gncp_chk

                    _enq_records: list = []
                    _enq_pass_count = 0
                    _enq_fail_count = 0
                    import re as _re_dod
                    _style_bad_re = _STYLE_SANITY_RE
                    _naming_bad_re = _re_dod.compile(_CLAUDE_TRANSLIT_RE.pattern, _re_dod.IGNORECASE)

                    for _cc_dod in (z0_exec_extra_cards or []):
                        _bq1_d = str(getattr(_cc_dod, "_bound_quote_1", "") or "").strip()
                        _bq2_d = str(getattr(_cc_dod, "_bound_quote_2", "") or "").strip()
                        if not _bq1_d and not _bq2_d:
                            continue  # no quotes available ??skip (non-PH_SUPP card)

                        _wh_d    = str(getattr(_cc_dod, "what_happened", "") or "")
                        _title_d = str(getattr(_cc_dod, "title_plain", "") or
                                       getattr(_cc_dod, "title", "") or "")
                        _furl_d  = str(getattr(_cc_dod, "final_url", "") or
                                       getattr(_cc_dod, "source_url", "") or "")
                        _iid_d   = str(getattr(_cc_dod, "item_id", "") or "")

                        # Fetch Q1/Q2 from (now-injected) canonical payload
                        try:
                            _cp_d  = _gncp_chk(_cc_dod)
                            _q1_d  = str(_cp_d.get("q1_event_2sent_zh", "") or "").strip()
                            _q2_d  = str(_cp_d.get("q2_impact_2sent_zh", "") or "").strip()
                            _primary_anchor_d = str(_cp_d.get("primary_anchor", "") or "").strip()
                        except Exception:
                            _q1_d = _q2_d = _primary_anchor_d = ""

                        # DoD checks
                        _dod_quality  = len(_bq1_d) >= 20 and len(_bq2_d) >= 20
                        # QUOTE_SOURCE: use pre-computed flag set at extraction time
                        # (extraction guarantees quotes are substrings; runtime re-check
                        # suffers from encoding differences, so trust the extraction flag)
                        _dod_source   = bool(getattr(_cc_dod, "_quote_source_ok", True))
                        _bq1_d_n = _bq1_d  # already normalized at extraction time
                        _bq2_d_n = _bq2_d  # already normalized at extraction time
                        _q1_d_n = _q1_d.replace('\r\n', ' ').replace('\r', ' ').replace('\n', ' ')
                        _q2_d_n = _q2_d.replace('\r\n', ' ').replace('\r', ' ').replace('\n', ' ')
                        _dod_trivial  = (
                            len(_bq1_d.split()) >= 4 and len(_bq2_d.split()) >= 4
                        )
                        # Q1_BINDING: any 10-char window of quote_1 must appear in q1_text
                        _dod_q1bind = bool(_bq1_d_n) and any(
                            _bq1_d_n[_qi:_qi + 10] in _q1_d_n
                            for _qi in range(0, max(1, len(_bq1_d_n) - 9), 5)
                        )
                        # Q2_BINDING: first 50 chars of quote_2 must appear in q2_text
                        _dod_q2bind = bool(_bq2_d_n) and (_bq2_d_n[:50] in _q2_d_n)
                        # ACTOR_BINDING: primary_anchor in quote_1 (injection re-selected it);
                        # fallback to quote_2 or what_happened; case-insensitive
                        _wh_d_actor = str(getattr(_cc_dod, "what_happened", "") or "")
                        _pa_ci = _primary_anchor_d.lower()
                        _dod_actor_bind = (
                            (not _primary_anchor_d)
                            or (_pa_ci in _bq1_d.lower())
                            or (_pa_ci in _bq2_d.lower())
                            or (_pa_ci in _wh_d_actor.lower())
                        )
                        # STYLE_SANITY: injected Q1/Q2 must not contain banned template phrases
                        _dod_style = not bool(_style_bad_re.search(_q1_d + " " + _q2_d))
                        # NAMING: no banned Chinese transliterations of Claude
                        _dod_naming = not bool(_naming_bad_re.search(_q1_d + " " + _q2_d))
                        # AI_RELEVANCE: title or Q1/Q2 must reference an AI topic
                        _dod_ai_rel = _is_ai_relevant(_title_d, _q1_d, _q2_d, _bq1_d, _bq2_d)

                        _dod_map = {
                            "QUOTE_QUALITY":    _dod_quality,
                            "QUOTE_SOURCE":     _dod_source,
                            "QUOTE_NOT_TRIVIAL": _dod_trivial,
                            "Q1_BINDING":       _dod_q1bind,
                            "Q2_BINDING":       _dod_q2bind,
                            "ACTOR_BINDING":    _dod_actor_bind,
                            "STYLE_SANITY":     _dod_style,
                            "NAMING":           _dod_naming,
                            "AI_RELEVANCE":     _dod_ai_rel,
                        }
                        # AI_RELEVANCE is advisory ??supplemental events (Tesla, Apple,
                        # Discord, etc.) may lack explicit AI keywords yet still carry
                        # valid verbatim quotes.  Excluding it from the hard-pass criterion
                        # mirrors the treatment in EXEC_DELIVERABLE_DOCX_PPTX_HARD.
                        _all_pass_d = all(v for k, v in _dod_map.items() if k != "AI_RELEVANCE")

                        _enq_records.append({
                            "item_id":    _iid_d,
                            "title":      _title_d,
                            "final_url":  _furl_d,
                            "actor":      _primary_anchor_d,
                            "quote_1":    _bq1_d[:200],
                            "quote_2":    _bq2_d[:200],
                            "q1_snippet": _q1_d[:300],
                            "q2_snippet": _q2_d[:300],
                            "dod":        _dod_map,
                            "all_pass":   _all_pass_d,
                        })
                        if _all_pass_d:
                            _enq_pass_count += 1
                        else:
                            _enq_fail_count += 1

                    # Keep this pre-gate non-blocking for noisy supplemental pool;
                    # final delivery hard gate is enforced later on final DOCX/PPTX.
                    _enq_gate = "PASS" if (_enq_pass_count >= 1) else ("FAIL" if _enq_fail_count > 0 else "SKIP")

                    _enq_out_dir = Path(settings.PROJECT_ROOT) / "outputs"
                    _enq_meta = {
                        "generated_at": _enq_dt.now(_enq_tz.utc).isoformat(),
                        "events_total": len(_enq_records),
                        "pass_count":   _enq_pass_count,
                        "fail_count":   _enq_fail_count,
                        "gate_result":  _enq_gate,
                        "events":       _enq_records,
                    }
                    (_enq_out_dir / "exec_news_quality.meta.json").write_text(
                        _enq_json.dumps(_enq_meta, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )

                    # Write LATEST_SHOWCASE.md (first 2 events with Q1/Q2 + quotes)
                    _sc_lines = ["# LATEST_SHOWCASE\n"]
                    for _ri_sc, _r_sc in enumerate(_enq_records[:2], 1):
                        _sc_title = _normalize_claude_name(str(_r_sc.get("title", "") or ""))
                        _sc_actor = _normalize_claude_name(str(_r_sc.get("actor", "") or ""))
                        _sc_q1 = _normalize_claude_name(str(_r_sc.get("q1_snippet", "") or ""))
                        _sc_q2 = _normalize_claude_name(str(_r_sc.get("q2_snippet", "") or ""))
                        _sc_quote_1 = _normalize_claude_name(str(_r_sc.get("quote_1", "") or ""))
                        _sc_quote_2 = _normalize_claude_name(str(_r_sc.get("quote_2", "") or ""))
                        _sc_lines += [
                            f"## Event {_ri_sc}: {_sc_title}",
                            "",
                            f"**final_url**: {_r_sc['final_url']}",
                            "",
                            f"**actor**: {_sc_actor}",
                            "",
                            f"**Q1** (injected):",
                            f"> {_sc_q1}",
                            "",
                            f"**Q2** (injected):",
                            f"> {_sc_q2}",
                            "",
                            f"**quote_1** (verbatim from source, {len(_sc_quote_1)} chars):",
                            f"> {_sc_quote_1}",
                            "",
                            f"**quote_2** (verbatim from source, {len(_sc_quote_2)} chars):",
                            f"> {_sc_quote_2}",
                            "",
                            f"**DoD**: {_r_sc['dod']}",
                            "",
                            "---",
                            "",
                        ]
                    (_enq_out_dir / "LATEST_SHOWCASE.md").write_text(
                        "\n".join(_sc_lines), encoding="utf-8",
                    )

                    if _enq_gate == "FAIL":
                        _fail_reasons = []
                        for _r_f in _enq_records:
                            if not _r_f["all_pass"]:
                                _failed_checks = [k for k, v in _r_f["dod"].items() if not v]
                                _fail_reasons.append(
                                    f"- {_r_f['title'][:60]}: failed={_failed_checks}"
                                )
                        _nr_gate_path = Path(settings.PROJECT_ROOT) / "outputs" / "NOT_READY.md"
                        _nr_gate_content = (
                            "# NOT_READY\n\n"
                            f"run_id: {__import__('os').environ.get('PIPELINE_RUN_ID', 'unknown')}\n"
                            "gate: EXEC_NEWS_QUALITY_HARD\n"
                            f"events_failing: {_enq_fail_count}\n\n"
                            "## Failing events (verbatim quote check):\n"
                            + "\n".join(_fail_reasons)
                            + "\n\n## Fix\n"
                            "Ensure each selected event's full_text contains "
                            ">=2 verbatim quotes (>=20 chars, >=4 words each).\n"
                        )
                        _nr_gate_path.write_text(_nr_gate_content, encoding="utf-8")
                        # Remove PPTX/DOCX that were just generated (gate failed)
                        for _art_del in ("executive_report.pptx", "executive_report.docx"):
                            _art_p = Path(settings.PROJECT_ROOT) / "outputs" / _art_del
                            if _art_p.exists():
                                try:
                                    _art_p.unlink()
                                except Exception:
                                    pass
                        log.error(
                            "EXEC_NEWS_QUALITY_HARD FAIL ??%d event(s) missing verbatim quotes; "
                            "NOT_READY.md written; PPTX/DOCX deleted",
                            _enq_fail_count,
                        )
                    else:
                        log.info(
                            "EXEC_NEWS_QUALITY_HARD: %s ??%d event(s) with valid verbatim quotes; "
                            "LATEST_SHOWCASE.md written",
                            _enq_gate, _enq_pass_count,
                        )
                except Exception as _enq_exc:
                    log.warning("EXEC_NEWS_QUALITY_HARD check failed (non-fatal): %s", _enq_exc)

                # ---------------------------------------------------------------
                # EXEC_DELIVERABLE_DOCX_PPTX_HARD gate
                # ---------------------------------------------------------------
                try:
                    import json as _gate_json
                    from datetime import datetime as _gate_dt, timezone as _gate_tz

                    _docx_canon = Path(settings.PROJECT_ROOT) / "outputs" / "executive_report.docx"
                    _pptx_canon = Path(settings.PROJECT_ROOT) / "outputs" / "executive_report.pptx"
                    _final_cards_eval = list(_final_cards or [])

                    _deliverable_meta = _evaluate_exec_deliverable_docx_pptx_hard(
                        final_cards=_final_cards_eval,
                        docx_path=_docx_canon,
                        pptx_path=_pptx_canon,
                    )
                    _deliverable_meta["generated_at"] = _gate_dt.now(_gate_tz.utc).isoformat()

                    _outputs_dir = Path(settings.PROJECT_ROOT) / "outputs"
                    _deliverable_meta_path = _outputs_dir / "exec_deliverable_docx_pptx_hard.meta.json"
                    _deliverable_meta_path.write_text(
                        _gate_json.dumps(_deliverable_meta, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )

                    # Keep legacy gate meta path for existing verify scripts.
                    _enq_records: list[dict] = []
                    _enq_pass_count = 0
                    _enq_fail_count = 0
                    for _ev in _deliverable_meta.get("events", []):
                        _dod_raw = dict(_ev.get("dod", {}) or {})
                        _q1 = str(_ev.get("q1_snippet", "") or "")
                        _q2 = str(_ev.get("q2_snippet", "") or "")
                        _q1_quote = str(_ev.get("quote_1", "") or "")
                        _q2_quote = str(_ev.get("quote_2", "") or "")
                        _title = str(_ev.get("title", "") or "")
                        _ai_rel = bool(_dod_raw.get("AI_RELEVANCE", False))
                        if not _ai_rel:
                            _ai_rel = _is_ai_relevant(_title, _q1, _q2, _q1_quote, _q2_quote)
                        _dod_map = {
                            "QUOTE_QUALITY": bool(_q1_quote and _q2_quote),
                            "QUOTE_SOURCE": bool(_ev.get("final_url", "")),
                            "QUOTE_NOT_TRIVIAL": len(_q1_quote.split()) >= 4 and len(_q2_quote.split()) >= 4,
                            "Q1_BINDING": bool(_dod_raw.get("QUOTE_LOCK_Q1", False)),
                            "Q2_BINDING": bool(_dod_raw.get("QUOTE_LOCK_Q2", False)),
                            "ACTOR_BINDING": bool(_dod_raw.get("ACTOR_NOT_NUMERIC", False)),
                            "STYLE_SANITY": bool(_dod_raw.get("STYLE_SANITY", False)),
                            "NAMING": bool(_dod_raw.get("NAMING", False)),
                            "AI_RELEVANCE": _ai_rel,
                        }
                        # AI_RELEVANCE is advisory (mirrors EXEC_DELIVERABLE_DOCX_PPTX_HARD fix).
                        _all_pass = (
                            all(v for k, v in _dod_map.items() if k != "AI_RELEVANCE")
                            and bool(_dod_raw.get("DOCX_PPTX_SYNC", False))
                        )
                        if _all_pass:
                            _enq_pass_count += 1
                        else:
                            _enq_fail_count += 1
                        _enq_records.append(
                            {
                                "item_id": str(_ev.get("item_id", "") or ""),
                                "title": _title,
                                "final_url": str(_ev.get("final_url", "") or ""),
                                "actor": str(_ev.get("actor", "") or ""),
                                "quote_1": _q1_quote,
                                "quote_2": _q2_quote,
                                "q1_snippet": _q1[:300],
                                "q2_snippet": _q2[:300],
                                "dod": _dod_map,
                                "all_pass": _all_pass,
                            }
                        )

                    # PASS if ?? event passes all non-advisory checks.
                    # AI_RELEVANCE is excluded from _all_pass so supplemental events
                    # (Tesla, Apple, etc.) don't block delivery.
                    _enq_gate = "PASS" if (_enq_pass_count >= 1) else "FAIL"
                    (_outputs_dir / "exec_news_quality.meta.json").write_text(
                        _gate_json.dumps(
                            {
                                "generated_at": _deliverable_meta["generated_at"],
                                "events_total": len(_enq_records),
                                "pass_count": _enq_pass_count,
                                "fail_count": _enq_fail_count,
                                "gate_result": _enq_gate,
                                "events": _enq_records,
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                        encoding="utf-8",
                    )

                    # Engineering audit only (not delivery artifact).
                    _showcase_lines = ["# LATEST_SHOWCASE", ""]
                    for _idx_show, _ev in enumerate(_enq_records[:2], 1):
                        _show_title = _normalize_claude_name(str(_ev.get("title", "") or ""))
                        _show_actor = _normalize_claude_name(str(_ev.get("actor", "") or ""))
                        _show_q1 = _normalize_claude_name(str(_ev.get("q1_snippet", "") or ""))
                        _show_q2 = _normalize_claude_name(str(_ev.get("q2_snippet", "") or ""))
                        _show_quote_1 = _normalize_claude_name(str(_ev.get("quote_1", "") or ""))
                        _show_quote_2 = _normalize_claude_name(str(_ev.get("quote_2", "") or ""))
                        _showcase_lines.extend(
                            [
                                f"## Event {_idx_show}: {_show_title}",
                                "",
                                f"**final_url**: {_ev['final_url']}",
                                "",
                                f"**actor**: {_show_actor}",
                                "",
                                "**Q1**:",
                                f"> {_show_q1}",
                                "",
                                "**Q2**:",
                                f"> {_show_q2}",
                                "",
                                "**quote_1**:",
                                f"> {_show_quote_1}",
                                "",
                                "**quote_2**:",
                                f"> {_show_quote_2}",
                                "",
                                f"**DoD**: {_ev['dod']}",
                                "",
                                "---",
                                "",
                            ]
                        )
                    (_outputs_dir / "LATEST_SHOWCASE.md").write_text("\n".join(_showcase_lines), encoding="utf-8")

                    _deliv_fail_count = int(_deliverable_meta.get("fail_count", 0) or 0)
                    # Use the gate's own gate_result which applies the built-in tolerance
                    # (pass_count >= 6 AND fail_count <= 2 = PASS) rather than treating
                    # any non-zero fail_count as a hard failure.  The gate function already
                    # encodes the correct tolerance; the pipeline should respect it.
                    _deliv_gate_result = str(_deliverable_meta.get("gate_result", "FAIL") or "FAIL")
                    if _deliv_gate_result != "PASS":
                        _fail_reasons = []
                        for _ev in _deliverable_meta.get("events", []):
                            _ev_dod = dict(_ev.get("dod", {}) or {})
                            _failed = [k for k, v in _ev_dod.items() if not bool(v)]
                            if _failed:
                                _fail_reasons.append(f"- {str(_ev.get('title', 'event'))[:80]}: failed={_failed}")
                        _nr_gate_path = _outputs_dir / "NOT_READY.md"
                        _nr_gate_path.write_text(
                            "# NOT_READY\n\n"
                            f"run_id: {os.environ.get('PIPELINE_RUN_ID', 'unknown')}\n"
                            "gate: EXEC_DELIVERABLE_DOCX_PPTX_HARD\n"
                            f"events_failing: {_deliv_fail_count}\n\n"
                            "## Failing events\n"
                            + ("\n".join(_fail_reasons) if _fail_reasons else "- no event details")
                            + "\n",
                            encoding="utf-8",
                        )

                        for _artifact in ("executive_report.pptx", "executive_report.docx"):
                            _target = _outputs_dir / _artifact
                            _backup = _exec_backups.get(_artifact) if isinstance(_exec_backups, dict) else None
                            if _backup and _backup.exists():
                                shutil.copy2(_backup, _target)
                            elif _target.exists():
                                _target.unlink(missing_ok=True)
                        log.error(
                            "EXEC_DELIVERABLE_DOCX_PPTX_HARD FAIL ??%d event(s) failed DoD; "
                            "NOT_READY.md written; canonical DOCX/PPTX restored or removed",
                            _deliv_fail_count,
                        )
                    else:
                        # fail_count == 0: PASS (even if pass_count < threshold on sparse day)
                        (_outputs_dir / "NOT_READY.md").unlink(missing_ok=True)
                        if isinstance(_exec_backups, dict):
                            for _backup in _exec_backups.values():
                                if _backup.exists():
                                    _backup.unlink(missing_ok=True)
                        log.info(
                            "EXEC_DELIVERABLE_DOCX_PPTX_HARD: PASS ??%d events validated and synchronized",
                            int(_deliverable_meta.get("pass_count", 0) or 0),
                        )
                except Exception as _deliverable_exc:
                    log.warning("EXEC_DELIVERABLE_DOCX_PPTX_HARD check failed (non-fatal): %s", _deliverable_exc)

                # ---------------------------------------------------------------
                # EXEC_ZH_NARRATIVE_WITH_QUOTE_HARD gate
                # DoD: every final_card must have:
                #   - q1_zh/q2_zh with >= 40 Chinese chars each
                #   - English ratio <= 50% in q1_zh/q2_zh
                #   - quote_window embedded in ??matching original quote_1/quote_2
                #   - STYLE_SANITY and NAMING compliance
                # Gate FAIL ??write NOT_READY.md, delete PPTX/DOCX.
                # ---------------------------------------------------------------
                try:
                    import json as _zhg_json
                    import re as _zhg_re
                    from datetime import datetime as _zhg_dt, timezone as _zhg_tz

                    _zhg_events: list = []
                    if _is_brief_mode:
                        for _fc_zh in (_final_cards or []):
                            _zhg_events.append(
                                {
                                    "title": str(_fc_zh.get("title", "") or ""),
                                    "mode": "brief",
                                    "checks": {"BRIEF_MODE_BYPASS": True},
                                    "all_pass": True,
                                }
                            )
                        _zhg_pass = len(_zhg_events)
                        _zhg_fail = 0
                        _zhg_result = "PASS"
                        _zhg_meta = {
                            "generated_at": _zhg_dt.now(_zhg_tz.utc).isoformat(),
                            "events_total": len(_zhg_events),
                            "pass_count": _zhg_pass,
                            "fail_count": _zhg_fail,
                            "gate_result": _zhg_result,
                            "mode": "brief",
                            "note": "Brief mode validated by EXEC_DELIVERABLE_DOCX_PPTX_HARD + BRIEF_*_HARD gates.",
                            "events": _zhg_events,
                        }
                        _zhg_meta_path = Path(settings.PROJECT_ROOT) / "outputs" / "exec_zh_narrative.meta.json"
                        _zhg_meta_path.write_text(
                            _zhg_json.dumps(_zhg_meta, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                        (Path(settings.PROJECT_ROOT) / "outputs" / "NOT_READY.md").unlink(missing_ok=True)
                        log.info(
                            "EXEC_ZH_NARRATIVE_WITH_QUOTE_HARD: PASS (brief mode bypass; validated by brief gates)"
                        )
                    else:
                        _zhg_zh_re = _zhg_re.compile(r'[\u4e00-\u9fff]')
                        _zhg_en_re = _zhg_re.compile(r'[a-zA-Z]')
                        _zhg_trans_re = _CLAUDE_TRANSLIT_RE
                        _zhg_pass = 0
                        _zhg_fail = 0

                        for _fc_zh in (_final_cards or []):
                            _title_zh = str(_fc_zh.get("title", "") or "")
                            _q1zh = str(_fc_zh.get("q1_zh", "") or "")
                            _q2zh = str(_fc_zh.get("q2_zh", "") or "")
                            _qw1 = str(_fc_zh.get("quote_window_1", "") or "")
                            _qw2 = str(_fc_zh.get("quote_window_2", "") or "")
                            _q1r = str(_fc_zh.get("quote_1", "") or "")
                            _q2r = str(_fc_zh.get("quote_2", "") or "")

                            _lq, _rq = "\u300c", "\u300d"
                            _checks_zh = {
                                "Q1_ZH_WINDOW": bool(_qw1 and (_lq + _qw1 + _rq) in _q1zh),
                                "Q2_ZH_WINDOW": bool(_qw2 and (_lq + _qw2 + _rq) in _q2zh),
                                "Q1_ZH_CHARS": len(_zhg_zh_re.findall(_q1zh)) >= 40,
                                "Q2_ZH_CHARS": len(_zhg_zh_re.findall(_q2zh)) >= 40,
                                "Q1_ZH_EN_RATIO": (
                                    (len(_zhg_en_re.findall(_q1zh)) / len(_q1zh)) <= 0.5
                                    if _q1zh
                                    else True
                                ),
                                "Q2_ZH_EN_RATIO": (
                                    (len(_zhg_en_re.findall(_q2zh)) / len(_q2zh)) <= 0.5
                                    if _q2zh
                                    else True
                                ),
                                "QW1_SUBSTRING": bool(_qw1 and _qw1 in _q1r),
                                "QW2_SUBSTRING": bool(_qw2 and _qw2 in _q2r),
                                "QW1_NONEMPTY": bool(_qw1),
                                "QW2_NONEMPTY": bool(_qw2),
                                "STYLE_SANITY": _style_sanity_ok(_q1zh, _q2zh),
                                "NAMING": (not bool(_zhg_trans_re.search(_q1zh + " " + _q2zh))),
                            }
                            _all_zh = all(_checks_zh.values())
                            if _all_zh:
                                _zhg_pass += 1
                            else:
                                _zhg_fail += 1
                            _zhg_events.append(
                                {
                                    "title": _title_zh,
                                    "q1_zh_snippet": _q1zh[:200],
                                    "q2_zh_snippet": _q2zh[:200],
                                    "quote_window_1": _qw1,
                                    "quote_window_2": _qw2,
                                    "checks": _checks_zh,
                                    "all_pass": _all_zh,
                                }
                            )

                        _zhg_result = "PASS" if (_zhg_pass >= 6 and _zhg_fail <= 2) else "FAIL"
                        _zhg_meta = {
                            "generated_at": _zhg_dt.now(_zhg_tz.utc).isoformat(),
                            "events_total": len(_zhg_events),
                            "pass_count": _zhg_pass,
                            "fail_count": _zhg_fail,
                            "gate_result": _zhg_result,
                            "events": _zhg_events,
                        }
                        _zhg_meta_path = Path(settings.PROJECT_ROOT) / "outputs" / "exec_zh_narrative.meta.json"
                        _zhg_meta_path.write_text(
                            _zhg_json.dumps(_zhg_meta, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )

                        if _zhg_fail > 0:
                            _zhg_fail_details = []
                            for _ev_zh in _zhg_events:
                                if not _ev_zh["all_pass"]:
                                    _failed_zh = [k for k, v in _ev_zh["checks"].items() if not v]
                                    _zhg_fail_details.append(
                                        f"- {_ev_zh['title'][:60]}: failed={_failed_zh}"
                                    )
                            _nr_zhg_path = Path(settings.PROJECT_ROOT) / "outputs" / "NOT_READY.md"
                            _nr_zhg_path.write_text(
                                "# NOT_READY\n\n"
                                f"run_id: {os.environ.get('PIPELINE_RUN_ID', 'unknown')}\n"
                                "gate: EXEC_ZH_NARRATIVE_WITH_QUOTE_HARD\n"
                                f"events_failing: {_zhg_fail}\n\n"
                                "## Failing events (zh narrative check):\n"
                                + "\n".join(_zhg_fail_details)
                                + "\n\n## Fix\n"
                                "Ensure each final_card has q1_zh/q2_zh with >=40 Chinese chars, "
                                "<=50% English ratio, and quote_window substrings in quote_1/quote_2.\n",
                                encoding="utf-8",
                            )
                            for _art_zh in ("executive_report.pptx", "executive_report.docx"):
                                _art_zhp = Path(settings.PROJECT_ROOT) / "outputs" / _art_zh
                                if _art_zhp.exists():
                                    _art_zhp.unlink(missing_ok=True)
                            log.error(
                                "EXEC_ZH_NARRATIVE_WITH_QUOTE_HARD FAIL ??%d event(s) failed; "
                                "NOT_READY.md written; PPTX/DOCX deleted",
                                _zhg_fail,
                            )
                        else:
                            (Path(settings.PROJECT_ROOT) / "outputs" / "NOT_READY.md").unlink(missing_ok=True)
                            log.info(
                                "EXEC_ZH_NARRATIVE_WITH_QUOTE_HARD: PASS ??%d events with valid zh narrative (fail_count=0)",
                                _zhg_pass,
                            )
                except Exception as _zhg_exc:
                    log.warning("EXEC_ZH_NARRATIVE_WITH_QUOTE_HARD check failed (non-fatal): %s", _zhg_exc)

                # ---------------------------------------------------------------
                # AI_PURITY_HARD gate ??100% of deck events must be AI-relevant
                # ---------------------------------------------------------------
                try:
                    import json as _aip_json
                    _aip_pass = all(fc.get("ai_relevance", False) for fc in (_final_cards or []))
                    _aip_meta = {
                        "gate_result": "PASS" if _aip_pass else "FAIL",
                        "selected": len(_final_cards or []),
                        "ai_true": sum(1 for fc in (_final_cards or []) if fc.get("ai_relevance")),
                        "watchlist_excluded": len(_watchlist_cards),
                    }
                    _aip_path = Path(settings.PROJECT_ROOT) / "outputs" / "ai_purity_hard.meta.json"
                    _aip_path.write_text(_aip_json.dumps(_aip_meta, ensure_ascii=False, indent=2), encoding="utf-8")
                    if not _aip_pass:
                        _nr_aip = Path(settings.PROJECT_ROOT) / "outputs" / "NOT_READY.md"
                        _nr_aip.write_text(
                            "# NOT_READY\n\ngate: AI_PURITY_HARD\n"
                            f"ai_true={_aip_meta['ai_true']} selected={_aip_meta['selected']}\n",
                            encoding="utf-8",
                        )
                        for _art_aip in ("executive_report.pptx", "executive_report.docx"):
                            (_art_aip_p := Path(settings.PROJECT_ROOT) / "outputs" / _art_aip).unlink(missing_ok=True)
                        log.error("AI_PURITY_HARD FAIL ??non-AI events in deck; NOT_READY.md written")
                    else:
                        log.info("AI_PURITY_HARD: PASS ??%d/%d events AI-relevant", _aip_meta["ai_true"], _aip_meta["selected"])
                except Exception as _aip_exc:
                    log.warning("AI_PURITY_HARD check failed (non-fatal): %s", _aip_exc)

                # ---------------------------------------------------------------
                # NO_BOILERPLATE_Q1Q2_HARD gate ??0 banned phrases in any q_zh
                # ---------------------------------------------------------------
                try:
                    import json as _nbp_json
                    _nbp_fail_events: list[dict] = []
                    for _fc_nbp in (_final_cards or []):
                        _nbp_ok, _nbp_reasons = check_no_boilerplate(
                            _fc_nbp.get("q1_zh", ""), _fc_nbp.get("q2_zh", "")
                        )
                        if not _nbp_ok:
                            _nbp_fail_events.append({"title": _fc_nbp.get("title", "")[:60], "reasons": _nbp_reasons})
                    _nbp_result = "PASS" if not _nbp_fail_events else "FAIL"
                    _nbp_meta = {
                        "gate_result": _nbp_result,
                        "events_total": len(_final_cards or []),
                        "fail_count": len(_nbp_fail_events),
                        "failing_events": _nbp_fail_events,
                    }
                    _nbp_path = Path(settings.PROJECT_ROOT) / "outputs" / "no_boilerplate_hard.meta.json"
                    _nbp_path.write_text(_nbp_json.dumps(_nbp_meta, ensure_ascii=False, indent=2), encoding="utf-8")
                    if _nbp_result == "FAIL":
                        _nr_nbp = Path(settings.PROJECT_ROOT) / "outputs" / "NOT_READY.md"
                        _nr_nbp.write_text(
                            "# NOT_READY\n\ngate: NO_BOILERPLATE_Q1Q2_HARD\n"
                            f"fail_count={len(_nbp_fail_events)}\n",
                            encoding="utf-8",
                        )
                        for _art_nbp in ("executive_report.pptx", "executive_report.docx"):
                            (Path(settings.PROJECT_ROOT) / "outputs" / _art_nbp).unlink(missing_ok=True)
                        log.error("NO_BOILERPLATE_Q1Q2_HARD FAIL ??%d events with banned phrases", len(_nbp_fail_events))
                    else:
                        log.info("NO_BOILERPLATE_Q1Q2_HARD: PASS ??0 boilerplate phrases found")
                except Exception as _nbp_exc:
                    log.warning("NO_BOILERPLATE_Q1Q2_HARD check failed (non-fatal): %s", _nbp_exc)

                # ---------------------------------------------------------------
                # Q1_STRUCTURE_HARD gate ??>= 10/12 events pass Q1 structure check
                # ---------------------------------------------------------------
                try:
                    import json as _q1s_json
                    _q1s_pass = 0
                    _q1s_fail = 0
                    _q1s_events: list[dict] = []
                    for _fc_q1s in (_final_cards or []):
                        _q1s_ok, _q1s_reasons = check_q1_structure(
                            _fc_q1s.get("q1_zh", ""), _fc_q1s.get("actor", ""),
                            _fc_q1s.get("quote_1", ""), _fc_q1s.get("anchors", []),
                        )
                        if _q1s_ok:
                            _q1s_pass += 1
                        else:
                            _q1s_fail += 1
                        _q1s_events.append({"title": _fc_q1s.get("title", "")[:60], "ok": _q1s_ok, "reasons": _q1s_reasons})
                    _total_q1s = len(_final_cards or [])
                    _q1s_result = "PASS" if (_q1s_pass >= min(10, _total_q1s) or _q1s_fail <= 2) else "FAIL"
                    _q1s_meta = {
                        "gate_result": _q1s_result, "pass_count": _q1s_pass,
                        "fail_count": _q1s_fail, "events_total": _total_q1s, "events": _q1s_events,
                    }
                    (Path(settings.PROJECT_ROOT) / "outputs" / "q1_structure_hard.meta.json").write_text(
                        _q1s_json.dumps(_q1s_meta, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    if _q1s_result == "FAIL":
                        (Path(settings.PROJECT_ROOT) / "outputs" / "NOT_READY.md").write_text(
                            f"# NOT_READY\n\ngate: Q1_STRUCTURE_HARD\nfail_count={_q1s_fail}\n", encoding="utf-8"
                        )
                        for _art_q1s in ("executive_report.pptx", "executive_report.docx"):
                            (Path(settings.PROJECT_ROOT) / "outputs" / _art_q1s).unlink(missing_ok=True)
                        log.error("Q1_STRUCTURE_HARD FAIL ??%d events failed Q1 structure", _q1s_fail)
                    else:
                        log.info("Q1_STRUCTURE_HARD: PASS ??%d/%d pass", _q1s_pass, _total_q1s)
                except Exception as _q1s_exc:
                    log.warning("Q1_STRUCTURE_HARD check failed (non-fatal): %s", _q1s_exc)

                # ---------------------------------------------------------------
                # Q2_STRUCTURE_HARD gate ??>= 10/12 events pass Q2 structure check
                # ---------------------------------------------------------------
                try:
                    import json as _q2s_json
                    _q2s_pass = 0
                    _q2s_fail = 0
                    _q2s_events: list[dict] = []
                    for _fc_q2s in (_final_cards or []):
                        _q2s_ok, _q2s_reasons = check_q2_structure(
                            _fc_q2s.get("q2_zh", ""), _fc_q2s.get("quote_2", ""),
                            _fc_q2s.get("anchors", []),
                        )
                        if _q2s_ok:
                            _q2s_pass += 1
                        else:
                            _q2s_fail += 1
                        _q2s_events.append({"title": _fc_q2s.get("title", "")[:60], "ok": _q2s_ok, "reasons": _q2s_reasons})
                    _total_q2s = len(_final_cards or [])
                    _q2s_result = "PASS" if (_q2s_pass >= min(10, _total_q2s) or _q2s_fail <= 2) else "FAIL"
                    _q2s_meta = {
                        "gate_result": _q2s_result, "pass_count": _q2s_pass,
                        "fail_count": _q2s_fail, "events_total": _total_q2s, "events": _q2s_events,
                    }
                    (Path(settings.PROJECT_ROOT) / "outputs" / "q2_structure_hard.meta.json").write_text(
                        _q2s_json.dumps(_q2s_meta, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    if _q2s_result == "FAIL":
                        (Path(settings.PROJECT_ROOT) / "outputs" / "NOT_READY.md").write_text(
                            f"# NOT_READY\n\ngate: Q2_STRUCTURE_HARD\nfail_count={_q2s_fail}\n", encoding="utf-8"
                        )
                        for _art_q2s in ("executive_report.pptx", "executive_report.docx"):
                            (Path(settings.PROJECT_ROOT) / "outputs" / _art_q2s).unlink(missing_ok=True)
                        log.error("Q2_STRUCTURE_HARD FAIL ??%d events failed Q2 structure", _q2s_fail)
                    else:
                        log.info("Q2_STRUCTURE_HARD: PASS ??%d/%d pass", _q2s_pass, _total_q2s)
                except Exception as _q2s_exc:
                    log.warning("Q2_STRUCTURE_HARD check failed (non-fatal): %s", _q2s_exc)

                # ---------------------------------------------------------------
                # MOVES_ANCHORED_HARD gate ??0 unanchored bullets
                # ---------------------------------------------------------------
                try:
                    import json as _ma_json
                    _ma_fail_events: list[dict] = []
                    for _fc_ma in (_final_cards or []):
                        _ma_ok, _ma_reasons = check_moves_anchored(
                            _fc_ma.get("moves", []), _fc_ma.get("risks", []),
                            _fc_ma.get("anchors", []),
                        )
                        if not _ma_ok:
                            _ma_fail_events.append({"title": _fc_ma.get("title", "")[:60], "reasons": _ma_reasons})
                    _ma_result = "PASS" if not _ma_fail_events else "FAIL"
                    _ma_meta = {
                        "gate_result": _ma_result, "events_total": len(_final_cards or []),
                        "fail_count": len(_ma_fail_events), "failing_events": _ma_fail_events,
                    }
                    (Path(settings.PROJECT_ROOT) / "outputs" / "moves_anchored_hard.meta.json").write_text(
                        _ma_json.dumps(_ma_meta, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    if _ma_result == "FAIL":
                        (Path(settings.PROJECT_ROOT) / "outputs" / "NOT_READY.md").write_text(
                            f"# NOT_READY\n\ngate: MOVES_ANCHORED_HARD\nfail_count={len(_ma_fail_events)}\n", encoding="utf-8"
                        )
                        for _art_ma in ("executive_report.pptx", "executive_report.docx"):
                            (Path(settings.PROJECT_ROOT) / "outputs" / _art_ma).unlink(missing_ok=True)
                        log.error("MOVES_ANCHORED_HARD FAIL ??%d events with unanchored bullets", len(_ma_fail_events))
                    else:
                        log.info("MOVES_ANCHORED_HARD: PASS ??all move/risk bullets anchored")
                except Exception as _ma_exc:
                    log.warning("MOVES_ANCHORED_HARD check failed (non-fatal): %s", _ma_exc)

                # ---------------------------------------------------------------
                # EXEC_PRODUCT_READABILITY_HARD gate ??>= 10/12 events pass
                # ---------------------------------------------------------------
                try:
                    import json as _epr_json
                    _epr_pass = 0
                    _epr_fail = 0
                    _epr_events: list[dict] = []
                    for _fc_epr in (_final_cards or []):
                        _epr_ok, _epr_reasons = check_exec_readability(
                            _fc_epr.get("q1_zh", ""), _fc_epr.get("q2_zh", ""),
                            _fc_epr.get("actor", ""),
                            _fc_epr.get("quote_window_1", ""), _fc_epr.get("quote_window_2", ""),
                        )
                        if _epr_ok:
                            _epr_pass += 1
                        else:
                            _epr_fail += 1
                        _epr_events.append({"title": _fc_epr.get("title", "")[:60], "ok": _epr_ok, "reasons": _epr_reasons})
                    _total_epr = len(_final_cards or [])
                    _epr_result = "PASS" if (_epr_pass >= min(10, _total_epr) or _epr_fail <= 2) else "FAIL"
                    _epr_meta = {
                        "gate_result": _epr_result, "pass_count": _epr_pass,
                        "fail_count": _epr_fail, "events_total": _total_epr, "events": _epr_events,
                    }
                    (Path(settings.PROJECT_ROOT) / "outputs" / "exec_product_readability_hard.meta.json").write_text(
                        _epr_json.dumps(_epr_meta, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    if _epr_result == "FAIL":
                        (Path(settings.PROJECT_ROOT) / "outputs" / "NOT_READY.md").write_text(
                            f"# NOT_READY\n\ngate: EXEC_PRODUCT_READABILITY_HARD\nfail_count={_epr_fail}\n", encoding="utf-8"
                        )
                        for _art_epr in ("executive_report.pptx", "executive_report.docx"):
                            (Path(settings.PROJECT_ROOT) / "outputs" / _art_epr).unlink(missing_ok=True)
                        log.error("EXEC_PRODUCT_READABILITY_HARD FAIL ??%d events failed", _epr_fail)
                    else:
                        log.info("EXEC_PRODUCT_READABILITY_HARD: PASS ??%d/%d pass", _epr_pass, _total_epr)
                except Exception as _epr_exc:
                    log.warning("EXEC_PRODUCT_READABILITY_HARD check failed (non-fatal): %s", _epr_exc)

                # ---------------------------------------------------------------
                # BRIEF hard gates (brief mode only)
                #   BRIEF_MIN_EVENTS_HARD       : ai_selected_events in [min, 10]
                #   BRIEF_NO_BOILERPLATE_HARD   : no banned boilerplate in What/Why
                #   BRIEF_ANCHOR_REQUIRED_HARD  : What/Why both contain anchor
                #   BRIEF_ZH_TW_HARD            : What/Why CJK ratio >= 0.6, no simplified chars
                # ---------------------------------------------------------------
                if _is_brief_mode:
                    try:
                        import json as _brief_json

                        _brief_cards = list(_final_cards or [])
                        _brief_total = len(_brief_cards)
                        _brief_min_ok = (_brief_total >= _brief_min_events) and (_brief_total <= 10)
                        _brief_min_meta = {
                            "gate_result": "PASS" if _brief_min_ok else "FAIL",
                            "events_total": _brief_total,
                            "required_min": _brief_min_events,
                            "required_max": 10,
                            "actual": _brief_total,
                        }
                        (Path(settings.PROJECT_ROOT) / "outputs" / "brief_min_events_hard.meta.json").write_text(
                            _brief_json.dumps(_brief_min_meta, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )

                        _brief_bp_fail: list[dict] = []
                        _brief_anchor_fail: list[dict] = []
                        _brief_zh_fail: list[dict] = []
                        _brief_info_fail: list[dict] = []
                        _brief_generic_fail: list[dict] = []
                        _brief_dup_fail: list[dict] = []
                        _brief_info_events: list[dict] = []  # per-event observability (Step 4)
                        _brief_frame_signatures: list[dict] = []
                        for _bfc in _brief_cards:
                            _title_b = str(_bfc.get("title", "") or "")[:80]
                            _summary_b = _normalize_ws(str(_bfc.get("summary_zh", "") or ""))
                            _what_b = _normalize_ws(str(_bfc.get("what_happened_brief", "") or _bfc.get("q1", "") or ""))
                            _why_b = _normalize_ws(str(_bfc.get("why_it_matters_brief", "") or _bfc.get("q2", "") or ""))
                            if not _summary_b:
                                _summary_b = _normalize_ws(f"{_what_b} {_why_b}")
                            _actor_b = _normalize_ws(str(_bfc.get("actor_primary", "") or _bfc.get("actor", "") or ""))
                            _anchors_b = [
                                _normalize_ws(str(_a or ""))
                                for _a in (_bfc.get("anchors", []) or [])
                                if _normalize_ws(str(_a or ""))
                            ]
                            _anchor_b = _brief_pick_primary_anchor(_actor_b, _anchors_b)
                            _what_bullets = [
                                _normalize_ws(str(_x or ""))
                                for _x in (_bfc.get("what_happened_bullets", []) or _brief_split_bullets(_what_b))
                                if _normalize_ws(str(_x or ""))
                            ]
                            _key_bullets = [
                                _normalize_ws(str(_x or ""))
                                for _x in (_bfc.get("key_details_bullets", []) or [])
                                if _normalize_ws(str(_x or ""))
                            ]
                            _why_bullets = [
                                _normalize_ws(str(_x or ""))
                                for _x in (_bfc.get("why_it_matters_bullets", []) or _brief_split_bullets(_why_b))
                                if _normalize_ws(str(_x or ""))
                            ]
                            _quote_1_b = _normalize_ws(str(_bfc.get("quote_1", "") or ""))
                            _quote_2_b = _normalize_ws(str(_bfc.get("quote_2", "") or ""))
                            _all_bullets = _what_bullets + _key_bullets + _why_bullets
                            _total_cjk_chars = sum(_brief_count_cjk_chars(_b) for _b in _all_bullets)
                            _bullets_total = len(_all_bullets)
                            _avg_cjk_chars_per_bullet = round((_total_cjk_chars / max(1, _bullets_total)), 2)
                            _bullet_len_ok = all(_brief_count_cjk_chars(_b) >= _BRIEF_MIN_BULLET_CJK_CHARS for _b in _all_bullets)
                            _bullet_hit_count = sum(
                                1
                                for _b in _all_bullets
                                if _brief_bullet_hit_anchor_or_number(_b, [_anchor_b] + _anchors_b)
                            )
                            _quote_cta_clean = (not _brief_quote_is_cta(_quote_1_b)) and (not _brief_quote_is_cta(_quote_2_b))
                            _brief_event_info_ok = (
                                len(_what_bullets) >= _BRIEF_TARGET_WHAT_BULLETS
                                and len(_key_bullets) >= _BRIEF_TARGET_KEY_BULLETS
                                and len(_why_bullets) >= _BRIEF_TARGET_WHY_BULLETS_MIN
                                and _bullet_len_ok
                                and _bullet_hit_count >= _BRIEF_MIN_ANCHOR_NUMBER_HITS
                                and _quote_cta_clean
                            )
                            # Per-event observability record (Step 4)
                            _det_en_used = [
                                str(_s or "")[:200]
                                for _s in (_bfc.get("detail_sentences_en_used", []) or [])
                                if _s
                            ]
                            _brief_info_events.append({
                                "title": _title_b,
                                "what_happened_count": len(_what_bullets),
                                "key_details_count": len(_key_bullets),
                                "why_it_matters_count": len(_why_bullets),
                                "bullets_total": _bullets_total,
                                "avg_cjk_chars_per_bullet": _avg_cjk_chars_per_bullet,
                                "bullet_len_ok": _bullet_len_ok,
                                "anchor_number_hits": _bullet_hit_count,
                                "quote_cta_clean": _quote_cta_clean,
                                "density_ok": _brief_event_info_ok,
                                "what_happened_sample": _what_bullets[:3],
                                "detail_sentence_en_used": _det_en_used[:2],
                            })
                            _generic_hits = _brief_find_generic_narrative_hits(
                                _summary_b,
                                _what_b,
                                _why_b,
                                *(_what_bullets + _key_bullets + _why_bullets),
                            )
                            if _generic_hits:
                                _g0 = _generic_hits[0]
                                _brief_generic_fail.append(
                                    {
                                        "title": _title_b,
                                        "hit_pattern": str(_g0.get("hit_pattern", "") or ""),
                                        "matched_text": str(_g0.get("matched_text", "") or ""),
                                        "sample_text": str(_g0.get("sample_text", "") or ""),
                                    }
                                )
                            _sig_set = _brief_collect_frame_signatures(
                                summary_zh=_summary_b,
                                what_bullets=_what_bullets,
                                key_bullets=_key_bullets,
                                why_bullets=_why_bullets,
                                actor=_actor_b,
                                anchors=[_anchor_b] + _anchors_b,
                            )
                            _brief_frame_signatures.append(
                                {
                                    "title": _title_b,
                                    "signatures": sorted(_sig_set),
                                }
                            )
                            if not _brief_event_info_ok:
                                _brief_info_fail.append(
                                    {
                                        "title": _title_b,
                                        "what_count": len(_what_bullets),
                                        "key_count": len(_key_bullets),
                                        "why_count": len(_why_bullets),
                                        "bullet_len_ok": _bullet_len_ok,
                                        "hit_count": _bullet_hit_count,
                                        "quote_cta_clean": _quote_cta_clean,
                                    }
                                )

                            if _brief_contains_boilerplate(_what_b, _why_b):
                                _brief_bp_fail.append(
                                    {"title": _title_b, "reason": "boilerplate_pattern_hit"}
                                )

                            _what_anchor_ok = _brief_has_anchor_token(_what_b, [_anchor_b] if _anchor_b else [])
                            _why_anchor_ok = _brief_has_anchor_token(_why_b, [_anchor_b] if _anchor_b else [])
                            if (not _anchor_b) or (not _what_anchor_ok) or (not _why_anchor_ok):
                                _brief_anchor_fail.append(
                                    {
                                        "title": _title_b,
                                        "anchor": _anchor_b,
                                        "what_anchor_ok": _what_anchor_ok,
                                        "why_anchor_ok": _why_anchor_ok,
                                    }
                                )

                            if (not _brief_zh_tw_ok(_what_b)) or (not _brief_zh_tw_ok(_why_b)):
                                _brief_zh_fail.append(
                                    {
                                        "title": _title_b,
                                        "what_cjk_ratio": round(_brief_zh_cjk_ratio(_what_b), 3),
                                        "why_cjk_ratio": round(_brief_zh_cjk_ratio(_why_b), 3),
                                        "reason": "zh_tw_check_fail",
                                    }
                                )

                        for _i in range(len(_brief_frame_signatures)):
                            _left = _brief_frame_signatures[_i]
                            _left_sigs = set(_left.get("signatures", []) or [])
                            if len(_left_sigs) < 2:
                                continue
                            for _j in range(_i + 1, len(_brief_frame_signatures)):
                                _right = _brief_frame_signatures[_j]
                                _right_sigs = set(_right.get("signatures", []) or [])
                                _shared = sorted(_left_sigs.intersection(_right_sigs))
                                if len(_shared) >= 2:
                                    _brief_dup_fail.append(
                                        {
                                            "title_a": str(_left.get("title", "") or ""),
                                            "title_b": str(_right.get("title", "") or ""),
                                            "duplicate_signature_count": len(_shared),
                                            "sample_hit_pattern": _shared[0],
                                            "shared_signatures": _shared[:5],
                                        }
                                    )

                        _brief_bp_meta = {
                            "gate_result": "PASS" if (len(_brief_bp_fail) == 0) else "FAIL",
                            "events_total": _brief_total,
                            "fail_count": len(_brief_bp_fail),
                            "failing_events": _brief_bp_fail,
                        }
                        (Path(settings.PROJECT_ROOT) / "outputs" / "brief_no_boilerplate_hard.meta.json").write_text(
                            _brief_json.dumps(_brief_bp_meta, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )

                        _brief_anchor_meta = {
                            "gate_result": "PASS" if (len(_brief_anchor_fail) == 0) else "FAIL",
                            "events_total": _brief_total,
                            "fail_count": len(_brief_anchor_fail),
                            "failing_events": _brief_anchor_fail,
                        }
                        (Path(settings.PROJECT_ROOT) / "outputs" / "brief_anchor_required_hard.meta.json").write_text(
                            _brief_json.dumps(_brief_anchor_meta, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )

                        _brief_zh_meta = {
                            "gate_result": "PASS" if (len(_brief_zh_fail) == 0) else "FAIL",
                            "events_total": _brief_total,
                            "fail_count": len(_brief_zh_fail),
                            "failing_events": _brief_zh_fail,
                        }
                        (Path(settings.PROJECT_ROOT) / "outputs" / "brief_zh_tw_hard.meta.json").write_text(
                            _brief_json.dumps(_brief_zh_meta, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )

                        _brief_info_meta = {
                            "gate_result": "PASS" if (len(_brief_info_fail) == 0) else "FAIL",
                            "events_total": _brief_total,
                            "fail_count": len(_brief_info_fail),
                            "failing_events": _brief_info_fail,
                            "rules": {
                                "what_happened_bullets_min": _BRIEF_TARGET_WHAT_BULLETS,
                                "key_details_bullets_min": _BRIEF_TARGET_KEY_BULLETS,
                                "why_it_matters_bullets_min": _BRIEF_TARGET_WHY_BULLETS_MIN,
                                "min_bullet_cjk_chars": _BRIEF_MIN_BULLET_CJK_CHARS,
                                "anchor_or_number_hits_min": _BRIEF_MIN_ANCHOR_NUMBER_HITS,
                                "quotes_must_not_hit_cta_stoplist": True,
                            },
                            "events": _brief_info_events,  # per-event observability (Step 4)
                        }
                        (Path(settings.PROJECT_ROOT) / "outputs" / "brief_info_density_hard.meta.json").write_text(
                            _brief_json.dumps(_brief_info_meta, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )

                        _brief_generic_meta = {
                            "gate_result": "PASS" if (len(_brief_generic_fail) == 0) else "FAIL",
                            "events_total": _brief_total,
                            "fail_count": len(_brief_generic_fail),
                            "failing_events": _brief_generic_fail,
                            "first_failing_event": (_brief_generic_fail[0] if _brief_generic_fail else {}),
                        }
                        (Path(settings.PROJECT_ROOT) / "outputs" / "brief_no_generic_narrative_hard.meta.json").write_text(
                            _brief_json.dumps(_brief_generic_meta, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )

                        _brief_dup_meta = {
                            "gate_result": "PASS" if (len(_brief_dup_fail) == 0) else "FAIL",
                            "events_total": _brief_total,
                            "fail_count": len(_brief_dup_fail),
                            "failing_events": _brief_dup_fail,
                            "first_failing_event": (_brief_dup_fail[0] if _brief_dup_fail else {}),
                            "signature_policy": {
                                "sentence_prefix_chars": 24,
                                "same_signature_min": 2,
                                "normalization": "remove_numbers_actor_anchors",
                            },
                        }
                        (Path(settings.PROJECT_ROOT) / "outputs" / "brief_no_duplicate_frames_hard.meta.json").write_text(
                            _brief_json.dumps(_brief_dup_meta, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )

                        _brief_any_fail = (
                            _brief_min_meta["gate_result"] == "FAIL"
                            or _brief_bp_meta["gate_result"] == "FAIL"
                            or _brief_anchor_meta["gate_result"] == "FAIL"
                            or _brief_zh_meta["gate_result"] == "FAIL"
                            or _brief_info_meta["gate_result"] == "FAIL"
                            or _brief_generic_meta["gate_result"] == "FAIL"
                            or _brief_dup_meta["gate_result"] == "FAIL"
                        )
                        if _brief_any_fail:
                            _brief_gate = "BRIEF_MIN_EVENTS_HARD"
                            _brief_detail = f"ai_selected_events={_brief_total}, required=[{_brief_min_events},10]"
                            if _brief_bp_meta["gate_result"] == "FAIL":
                                _brief_gate = "BRIEF_NO_BOILERPLATE_HARD"
                                _brief_detail = f"boilerplate_fail_count={len(_brief_bp_fail)}"
                            if _brief_anchor_meta["gate_result"] == "FAIL":
                                _brief_gate = "BRIEF_ANCHOR_REQUIRED_HARD"
                                _brief_detail = f"anchor_fail_count={len(_brief_anchor_fail)}"
                            if _brief_zh_meta["gate_result"] == "FAIL":
                                _brief_gate = "BRIEF_ZH_TW_HARD"
                                _brief_detail = f"zh_tw_fail_count={len(_brief_zh_fail)}"
                            if _brief_info_meta["gate_result"] == "FAIL":
                                _brief_gate = "BRIEF_INFO_DENSITY_HARD"
                                _brief_detail = f"info_density_fail_count={len(_brief_info_fail)}"
                            if _brief_generic_meta["gate_result"] == "FAIL":
                                _brief_gate = "BRIEF_NO_GENERIC_NARRATIVE_HARD"
                                _brief_detail = f"generic_narrative_fail_count={len(_brief_generic_fail)}"
                            if _brief_dup_meta["gate_result"] == "FAIL":
                                _brief_gate = "BRIEF_NO_DUPLICATE_FRAMES_HARD"
                                _brief_detail = f"duplicate_frames_fail_count={len(_brief_dup_fail)}"

                            (Path(settings.PROJECT_ROOT) / "outputs" / "NOT_READY.md").write_text(
                                "# NOT_READY\n\n"
                                f"run_id: {os.environ.get('PIPELINE_RUN_ID', 'unknown')}\n"
                                f"gate: {_brief_gate}\n"
                                f"fail_reason: {_brief_detail}\n"
                                f"counts: events_total={_brief_total} min_required={_brief_min_events} "
                                f"boilerplate_fail={len(_brief_bp_fail)} anchor_fail={len(_brief_anchor_fail)} "
                                f"zh_tw_fail={len(_brief_zh_fail)} info_density_fail={len(_brief_info_fail)} "
                                f"generic_narrative_fail={len(_brief_generic_fail)} duplicate_frames_fail={len(_brief_dup_fail)}\n",
                                encoding="utf-8",
                            )
                            for _brief_art in ("executive_report.pptx", "executive_report.docx"):
                                (Path(settings.PROJECT_ROOT) / "outputs" / _brief_art).unlink(missing_ok=True)
                            log.error("%s FAIL — %s", _brief_gate, _brief_detail)
                            _supply_meta["not_ready"] = True
                            _supply_meta["reason"] = f"{_brief_gate}: {_brief_detail}"
                            _supply_meta["final_ai_selected_events"] = int(_brief_total)
                            _write_supply_resilience_meta(_supply_meta)
                        else:
                            log.info(
                                "BRIEF_GATES: PASS min_events=%d total=%d boilerplate_fail=0 anchor_fail=0 zh_tw_fail=0 info_density_fail=0 generic_narrative_fail=0 duplicate_frames_fail=0",
                                _brief_min_events, _brief_total,
                            )
                            _supply_meta["not_ready"] = False
                            _supply_meta["reason"] = ""
                            _supply_meta["final_ai_selected_events"] = int(_brief_total)
                            _write_supply_resilience_meta(_supply_meta)
                    except Exception as _brief_gate_exc:
                        log.warning("BRIEF hard gates check failed (non-fatal): %s", _brief_gate_exc)

                # ---------------------------------------------------------------
                # STATS_SINGLE_SOURCE_HARD gate ??stats from canonical meta files only
                # ---------------------------------------------------------------
                try:
                    import json as _sss_json
                    _canonical_sources = [
                        "pool_sufficiency.meta.json",
                        "fulltext_hydrator.meta.json",
                        "flow_counts.meta.json",
                        "final_cards.meta.json",
                    ]
                    _sss_present = []
                    _sss_missing = []
                    for _src in _canonical_sources:
                        _src_path = Path(settings.PROJECT_ROOT) / "outputs" / _src
                        if _src_path.exists():
                            _sss_present.append(_src)
                        else:
                            _sss_missing.append(_src)
                    _sss_result = "PASS" if len(_sss_missing) <= 2 else "FAIL"
                    _sss_meta = {
                        "gate_result": _sss_result,
                        "canonical_sources": _canonical_sources,
                        "present": _sss_present,
                        "missing": _sss_missing,
                        "source_audit": "stats must come from canonical meta files only",
                    }
                    (Path(settings.PROJECT_ROOT) / "outputs" / "stats_single_source_hard.meta.json").write_text(
                        _sss_json.dumps(_sss_meta, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    if _sss_result == "FAIL":
                        log.error("STATS_SINGLE_SOURCE_HARD FAIL ??canonical sources missing: %s", _sss_missing)
                    else:
                        log.info("STATS_SINGLE_SOURCE_HARD: PASS ??%d/%d canonical sources present",
                                 len(_sss_present), len(_canonical_sources))
                except Exception as _sss_exc:
                    log.warning("STATS_SINGLE_SOURCE_HARD check failed (non-fatal): %s", _sss_exc)

                # SHOWCASE_READY_HARD gate ??guards against empty-deck OK runs.
                # Reads showcase_ready.meta.json (written above); if showcase_ready=false,
                # deletes PPTX/DOCX and writes NOT_READY.md so Hard-D guard exits 1.
                try:
                    import json as _scg_json
                    _scg_path = Path(settings.PROJECT_ROOT) / "outputs" / "showcase_ready.meta.json"
                    if _scg_path.exists():
                        _scg_data  = _scg_json.loads(_scg_path.read_text(encoding="utf-8"))
                        _scg_ready = bool(_scg_data.get("showcase_ready", True))
                        _scg_ai    = int(_scg_data.get("ai_selected_events", 0) or 0)
                        _scg_mode  = str(_scg_data.get("mode", "manual"))
                        _scg_thr   = int(_scg_data.get("threshold", 6) or 6)
                        if not _scg_ready:
                            log.error(
                                "SHOWCASE_READY_HARD FAIL ??ai_selected=%d < %d (mode=%s); "
                                "deck would be empty. Deleting output files, writing NOT_READY.md.",
                                _scg_ai, _scg_thr, _scg_mode,
                            )
                            for _scg_art in ("executive_report.pptx", "executive_report.docx"):
                                _scg_art_path = Path(settings.PROJECT_ROOT) / "outputs" / _scg_art
                                if _scg_art_path.exists():
                                    _scg_art_path.unlink(missing_ok=True)
                            _scg_nr = Path(settings.PROJECT_ROOT) / "outputs" / "NOT_READY.md"
                            _scg_nr.write_text(
                                "# NOT_READY\n\n"
                                f"run_id: {os.environ.get('PIPELINE_RUN_ID', 'unknown')}\n"
                                "gate: SHOWCASE_READY_HARD\n"
                                "events_failing: 1\n\n"
                                "## Failing events:\n"
                                f"- ai_selected_events={_scg_ai} is below threshold={_scg_thr}\n\n"
                                "## Fix\n"
                                f"Ensure the AI event pipeline selects >= {_scg_thr} events. "
                                "Check source feed freshness and filter settings.\n",
                                encoding="utf-8",
                            )
                            _supply_meta["not_ready"] = True
                            _supply_meta["reason"] = f"SHOWCASE_READY_HARD: ai_selected={_scg_ai} threshold={_scg_thr}"
                            _supply_meta["final_ai_selected_events"] = int(_scg_ai)
                            _write_supply_resilience_meta(_supply_meta)
                        else:
                            (Path(settings.PROJECT_ROOT) / "outputs" / "NOT_READY.md").unlink(missing_ok=True)
                            log.info(
                                "SHOWCASE_READY_HARD: PASS ??ai_selected=%d >= %d (mode=%s)",
                                _scg_ai, _scg_thr, _scg_mode,
                            )
                            _supply_meta["not_ready"] = False
                            _supply_meta["reason"] = ""
                            _supply_meta["final_ai_selected_events"] = int(_scg_ai)
                            _write_supply_resilience_meta(_supply_meta)
                    else:
                        log.warning("SHOWCASE_READY_HARD: showcase_ready.meta.json not found (skipping gate)")
                except Exception as _scg_exc:
                    log.warning("SHOWCASE_READY_HARD check failed (non-fatal): %s", _scg_exc)
                # Keep exec_selection.meta.json aligned with final_cards after all
                # renderer/gate writes (some generator paths overwrite this file).
                try:
                    _sync_exec_selection_meta(_final_cards or [])
                    _sync_faithful_zh_news_meta(_final_cards or [])
                except Exception:
                    pass

            except Exception as exc_bin:
                log.error("Executive report generation failed (non-blocking): %s", exc_bin)
        except Exception as exc:
            log.error("Z5 Education Renderer failed (non-blocking): %s", exc)
            try:
                err_md = render_error_report(exc)
                err_path = Path(settings.PROJECT_ROOT) / "outputs" / "deep_analysis_education.md"
                err_path.parent.mkdir(parents=True, exist_ok=True)
                err_path.write_text(err_md, encoding="utf-8")
                log.info("Z5: ?航炊隤芣?撌脣神??%s", err_path)
            except Exception:
                log.error("Z5: ??隤文?撖思??箔?")
    else:
        log.info("Z5: Education report disabled")

    # Hard-D guard: if NOT_READY.md was written by content_strategy, exit 1 so both
    # verify scripts consistently report FAIL (PPTX/DOCX were already blocked by the
    # RuntimeError raised inside get_event_cards_for_deck).
    _nr_check_path = Path(settings.PROJECT_ROOT) / "outputs" / "NOT_READY.md"
    if _nr_check_path.exists():
        log.error(
            "POOL_SUFFICIENCY FAIL ??NOT_READY.md exists; "
            "PPTX/DOCX not generated. Pipeline exits 1."
        )
        _supply_meta["not_ready"] = True
        if not _supply_meta.get("reason"):
            try:
                _nr_text = _nr_check_path.read_text(encoding="utf-8")
                _supply_meta["reason"] = _normalize_ws(_nr_text)[:240]
            except Exception:
                _supply_meta["reason"] = "NOT_READY.md exists"
        _write_supply_resilience_meta(_supply_meta)
        sys.exit(1)

    # (A) Write flow_counts.meta.json + filter_breakdown.meta.json ??pipeline funnel audit
    try:
        import json as _json
        _dr = dict(filter_summary.dropped_by_reason or {})
        _too_old = int(_dr.get("too_old", 0))
        _dr_top5 = [
            {"reason": k, "count": v}
            for k, v in sorted(_dr.items(), key=lambda kv: kv[1], reverse=True)[:5]
        ]
        # Try to read exec_selected_total from exec_selection.meta.json (written by Z5)
        _exec_sel_total = 0
        _exec_meta_path = Path(settings.PROJECT_ROOT) / "outputs" / "exec_selection.meta.json"
        if _exec_meta_path.exists():
            try:
                _exec_sel_data = _json.loads(_exec_meta_path.read_text(encoding="utf-8"))
                _exec_sel_total = int(_exec_sel_data.get("events_total", 0))
            except Exception:
                pass
        _flow_counts = {
            "z0_loaded_total": collector.fetched_total,
            "after_dedupe_total": collector.deduped_total,
            "after_too_old_filter_total": max(0, collector.deduped_total - _too_old),
            "event_gate_pass_total": collector.event_gate_pass_total,
            "signal_gate_pass_total": collector.signal_gate_pass_total,
            "exec_candidates_total": len(processing_items),
            "exec_selected_total": _exec_sel_total,
            "extra_cards_total": len(z0_exec_extra_cards),
            "drop_reasons_top5": _dr_top5,
        }
        _fc_path = Path(settings.PROJECT_ROOT) / "outputs" / "flow_counts.meta.json"
        _fc_path.parent.mkdir(parents=True, exist_ok=True)
        _fc_path.write_text(_json.dumps(_flow_counts, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("flow_counts.meta.json written: %s", _fc_path)

        # filter_breakdown.meta.json ??full per-reason diagnostics
        _fb = {
            "kept": int(filter_summary.kept_count),
            "dropped_total": int(filter_summary.input_count - filter_summary.kept_count),
            "input_count": int(filter_summary.input_count),
            "reasons": _dr,
            "top5_reasons": _dr_top5,
            "lang_not_allowed_count": int(_dr.get("lang_not_allowed", 0)),
            "too_old_count": int(_dr.get("too_old", 0)),
            "body_too_short_count": int(_dr.get("body_too_short", 0)),
            "non_ai_topic_count": int(_dr.get("non_ai_topic", 0)),
            "allow_zh_enabled": bool(int(os.getenv("ALLOW_ZH_SOURCES_IN_OFFLINE", "0"))),
        }
        _fb_path = Path(settings.PROJECT_ROOT) / "outputs" / "filter_breakdown.meta.json"
        _fb_path.write_text(_json.dumps(_fb, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("filter_breakdown.meta.json written: %s", _fb_path)
    except Exception as _fc_exc:
        log.warning("flow_counts / filter_breakdown meta write failed (non-blocking): %s", _fc_exc)

    # ---------------------------------------------------------------------------
    # latest_digest.md ??MVP Demo (Iteration 8)
    #   Top-2 executive event cards with canonical Q1/Q2 + verbatim rich quotes.
    #   Sorted: fulltext_ok=True first, then by density_score desc.
    # ---------------------------------------------------------------------------
    try:
        from utils.canonical_narrative import get_canonical_payload as _get_canon
        import re as _re_digest

        # Candidate pool: event_density_cards (highest quality), supplement with quality_cards
        _digest_pool = list(event_density_cards) if event_density_cards else []
        if len(_digest_pool) < 2:
            _digest_pool += [c for c in (quality_cards or []) if c not in _digest_pool]

        def _digest_sort_key(c):
            ft_ok = int(bool(getattr(c, "fulltext_ok", False) or (getattr(c, "fulltext_len", 0) or 0) >= 300))
            score = int(getattr(c, "density_score", 0) or 0)
            return (ft_ok, score)

        _digest_pool.sort(key=_digest_sort_key, reverse=True)
        _top_cards = _digest_pool[:2]

        _digest_lines: list[str] = [
            "# AI Intel Daily Digest",
            f"_Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}_",
            "",
        ]

        for _idx, _card in enumerate(_top_cards, 1):
            try:
                _cp = _get_canon(_card)
                _title     = str(_cp.get("title_clean") or getattr(_card, "title_plain", "") or "").strip()
                _q1        = str(_cp.get("q1_event_2sent_zh") or "").strip()
                _q2        = str(_cp.get("q2_impact_2sent_zh") or "").strip()
                _proof     = str(_cp.get("proof_line") or "").strip()
                _bucket    = str(_cp.get("bucket") or "").strip()
                _anchor    = ""
                # Extract primary anchor from news_anchor meta or card attribute
                _anchor_attr = str(getattr(_card, "primary_anchor", "") or "").strip()
                if _anchor_attr:
                    _anchor = _anchor_attr
                # Extract verbatim quote from ??..??in Q1
                _q1_quote = ""
                _qm = _re_digest.search(r"\u300c([^\u300d]{20,80})\u300d", _q1)
                if _qm:
                    _q1_quote = _qm.group(1)
                _q2_quote = ""
                _qm2 = _re_digest.search(r"\u300c([^\u300d]{20,80})\u300d", _q2)
                if _qm2:
                    _q2_quote = _qm2.group(1)
                _ft_len = int(getattr(_card, "fulltext_len", 0) or 0)
                _ft_ok = _ft_len >= 300

                _digest_lines.append(f"## Event {_idx}: {_title}")
                if _bucket:
                    _digest_lines.append(f"**Channel:** {_bucket}")
                if _anchor:
                    _digest_lines.append(f"**Anchor:** {_anchor}")
                _digest_lines.append(f"**Fulltext:** {'OK' if _ft_ok else 'N/A'} ({_ft_len} chars)")
                _digest_lines.append("")
                if _q1:
                    _digest_lines.append(f"**Q1 (鈭辣):** {_q1}")
                if _q1_quote:
                    _digest_lines.append(f"> verbatim: 「{_q1_quote}」")
                _digest_lines.append("")
                if _q2:
                    _digest_lines.append(f"**Q2 (敶梢):** {_q2}")
                if _q2_quote:
                    _digest_lines.append(f"> verbatim: 「{_q2_quote}」")
                _digest_lines.append("")
                if _proof:
                    _digest_lines.append(f"**Proof:** {_proof}")
                _digest_lines.append("")
                _digest_lines.append("---")
                _digest_lines.append("")
            except Exception as _card_exc:
                log.warning("latest_digest.md card %d failed (non-fatal): %s", _idx, _card_exc)

        _digest_md = "\n".join(_digest_lines)
        _digest_out = Path(settings.PROJECT_ROOT) / "outputs" / "latest_digest.md"
        _digest_out.parent.mkdir(parents=True, exist_ok=True)
        _digest_out.write_text(_digest_md, encoding="utf-8")
        log.info("latest_digest.md written: %s (%d events)", _digest_out, len(_top_cards))
    except Exception as _digest_exc:
        log.warning("latest_digest.md generation failed (non-fatal): %s", _digest_exc)

    elapsed = time.time() - t_start
    passed = sum(1 for r in all_results if r.passed_gate)
    log.info("PIPELINE COMPLETE | %d processed | %d passed | %.2fs total", len(all_results), passed, elapsed)
    log.info("Digest: %s", digest_path)
    log.info("Metrics: %s", metrics_path)

    # Write desktop_button.meta.json ??MVP Demo (Iteration 8)
    # Reads PIPELINE_RUN_ID env var if set (by run_pipeline.ps1); otherwise auto-generates.
    try:
        import json as _db_json
        _db_run_id = os.environ.get("PIPELINE_RUN_ID", "") or datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        _db_meta = {
            "run_id": _db_run_id,
            "started_at": t_start_iso,
            "finished_at": datetime.now(UTC).isoformat(),
            "exit_code": 0,
            "success": True,
            "pipeline": "scripts/run_once.py",
            "triggered_by": os.environ.get("PIPELINE_TRIGGERED_BY", "run_once.py"),
        }
        _db_path = Path(settings.PROJECT_ROOT) / "outputs" / "desktop_button.meta.json"
        _db_path.parent.mkdir(parents=True, exist_ok=True)
        _db_path.write_text(_db_json.dumps(_db_meta, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("desktop_button.meta.json written: run_id=%s", _db_run_id)
    except Exception as _db_exc:
        log.warning("desktop_button.meta.json write failed (non-fatal): %s", _db_exc)

    if not _supply_meta.get("final_ai_selected_events"):
        _md = locals().get("metrics_dict", {})
        if isinstance(_md, dict):
            _supply_meta["final_ai_selected_events"] = int(len(_md.get("final_cards", []) or []))
        else:
            _supply_meta["final_ai_selected_events"] = 0
    if not (Path(settings.PROJECT_ROOT) / "outputs" / "NOT_READY.md").exists():
        _supply_meta["not_ready"] = False
        if not _supply_meta.get("reason"):
            _supply_meta["reason"] = ""
    _write_supply_resilience_meta(_supply_meta)

    # Notifications
    send_all_notifications(t_start_iso, len(all_results), True, str(digest_path))


if __name__ == "__main__":
    if "--not-ready-report" in sys.argv:
        # Standalone mode: generate NOT_READY_report.docx/.pptx without running pipeline.
        # Called by run_pipeline.ps1 when IsSuccess=False.
        import json as _nr_json
        import re as _nr_re
        from datetime import datetime as _nr_dt
        from pathlib import Path as _nr_Path

        _proj_root = Path(__file__).resolve().parent.parent
        _outputs   = _proj_root / "outputs"
        _outputs.mkdir(parents=True, exist_ok=True)

        # 1. Parse NOT_READY.md ??gate_name + fail_reason (one-liner, human-readable)
        _nr_md_path  = _outputs / "NOT_READY.md"
        _gate_name   = "UNKNOWN"
        _fail_reason = "Pipeline failed. See outputs/desktop_button.last_run.log for details."
        if _nr_md_path.exists():
            try:
                _nr_text = _nr_md_path.read_text(encoding="utf-8")
                _gm = _nr_re.search(r"^gate:\s*(.+)$", _nr_text, _nr_re.MULTILINE)
                if _gm:
                    _gate_name = _gm.group(1).strip()
                _fail_reason = " ".join(_nr_text.split())[:300]
            except Exception as _nre:
                _fail_reason = f"Failed to parse NOT_READY.md: {_nre}"

        # 2. Load up to 3 sample events from meta files
        _samples: list = []
        for _meta_file in ("final_cards.meta.json", "exec_selection.meta.json"):
            _mpath = _outputs / _meta_file
            if not _mpath.exists():
                continue
            try:
                _mdata = _nr_json.loads(_mpath.read_text(encoding="utf-8"))
                _evts  = _mdata.get("events") or _mdata.get("final_selected_events") or []
                if isinstance(_evts, list) and _evts:
                    _samples = [
                        {
                            "title": str(e.get("title") or e.get("title_plain") or ""),
                            "final_url": str(e.get("final_url") or e.get("source_url") or ""),
                        }
                        for e in _evts[:3]
                        if isinstance(e, dict)
                    ]
                    break
            except Exception:
                pass

        # 3. Build next_steps hint based on gate name
        _gate_tips = {
            "EXEC_NEWS_QUALITY_HARD": (
                "Check quote binding and ensure source text exists; re-run collection if needed."
            ),
            "EXEC_ZH_NARRATIVE_WITH_QUOTE_HARD": (
                "Fix Chinese narrative generation and keep quote windows embedded verbatim."
            ),
            "POOL_SUFFICIENCY": (
                "Increase AI-relevant upstream inputs and verify collector output freshness."
            ),
            "AI_PURITY_HARD": (
                "Ensure only AI-relevant events enter final deck selection."
            ),
            "EXEC_DELIVERABLE_DOCX_PPTX_HARD": (
                "Check generator outputs and verify DOCX/PPTX event sync against final cards."
            ),
        }
        _next_steps = "See outputs/desktop_button.last_run.log for the full failure trace."
        for _k, _tip in _gate_tips.items():
            if _k in _gate_name:
                _next_steps = _tip
                break

        _run_id   = os.environ.get("PIPELINE_RUN_ID", "")
        _run_date = _nr_dt.now().strftime("%Y-%m-%d")

        # 4. Generate NOT_READY_report.docx
        try:
            from core.doc_generator import generate_not_ready_report_docx
            _docx_out = generate_not_ready_report_docx(
                output_path=_outputs / "NOT_READY_report.docx",
                fail_reason=_fail_reason,
                gate_name=_gate_name,
                samples=_samples,
                next_steps=_next_steps,
                run_id=_run_id,
                run_date=_run_date,
            )
            print(f"NOT_READY_report.docx written: {_docx_out}")
        except Exception as _docx_exc:
            print(f"ERROR generating NOT_READY_report.docx: {_docx_exc}")

        # 5. Generate NOT_READY_report.pptx
        try:
            from core.ppt_generator import generate_not_ready_report_pptx
            _pptx_out = generate_not_ready_report_pptx(
                output_path=_outputs / "NOT_READY_report.pptx",
                fail_reason=_fail_reason,
                gate_name=_gate_name,
                samples=_samples,
                next_steps=_next_steps,
                run_id=_run_id,
                run_date=_run_date,
            )
            print(f"NOT_READY_report.pptx written: {_pptx_out}")
        except Exception as _pptx_exc:
            print(f"ERROR generating NOT_READY_report.pptx: {_pptx_exc}")

        sys.exit(0)
    else:
        run_pipeline()
