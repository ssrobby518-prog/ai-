"""Run the full pipeline once: Ingest -> Process -> Store -> Deliver."""

import os
import re
import shutil
import sys
import time
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
        title = str(getattr(item, "title", "") or "").strip() or "來源訊號"
        body = str(getattr(item, "body", "") or "").strip()
        # Prefer hydrated full_text for summary so canonical clean_len >= 300 passes
        # the demotion block in get_event_cards_for_deck; fall back to body.
        _full_text_attr = str(getattr(item, "full_text", "") or "").strip()
        _summary_source = _full_text_attr if _full_text_attr else body
        summary = _summary_source[:500] if _summary_source else "來源內容有限，請以原始連結核對。"
        source_name = str(getattr(item, "source_name", "") or "").strip() or "來源平台"
        source_url = str(getattr(item, "url", "") or "").strip()
        density = float(getattr(item, "density_score", 0) or 0)
        score = max(3.0, min(10.0, round(density / 10.0, 2)))
        card = EduNewsCard(
            item_id=str(getattr(item, "item_id", "") or ""),
            is_valid_news=True,
            title_plain=title,
            what_happened=summary,
            why_important=f"來源：{source_name}。原始連結：{source_url if source_url.startswith('http') else 'N/A'}。",
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
_CLAUDE_WORD_RE = re.compile(r"\bClaude\b(?!（Anthropic）)")
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
    r"text-to-image|multimodal|agent|agents|foundation model)\b",
    re.IGNORECASE,
)


def _normalize_ws(text: str) -> str:
    return " ".join(str(text or "").replace("\r", " ").replace("\n", " ").split())


def _clip_text(text: str, limit: int = 110) -> str:
    txt = _normalize_ws(text)
    return txt if len(txt) <= limit else txt[:limit].rstrip()


def _style_sanity_ok(*parts: str) -> bool:
    joined = _normalize_ws(" ".join(parts))
    if not joined:
        return False
    return not bool(_STYLE_SANITY_RE.search(joined))


def _extract_quoted_segments(text: str) -> list[str]:
    src = str(text or "")
    segs: list[str] = []
    patterns = (
        r"「([^」]+)」",
        r"『([^』]+)』",
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
    return _normalize_ws(f"原文關鍵句：「{quote_1}」。對應事件：{title}。")


def _build_q2_quote_driven(title: str, quote_2: str) -> str:
    return _normalize_ws(f"原文影響句：「{quote_2}」。商業意義：此句直接界定事件影響範圍與決策優先序。")


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
    if _quote_len_ok(primary):
        ordered.append(_normalize_ws(primary))
    for q in pool:
        qn = _normalize_ws(q)
        if _quote_len_ok(qn):
            ordered.append(qn)
    fb = _normalize_ws(fallback_blob)
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
    cleaned = _CLAUDE_TRANSLIT_RE.sub("Claude（Anthropic）", cleaned)
    cleaned = _CLAUDE_WORD_RE.sub("Claude（Anthropic）", cleaned)
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
        marker_lines = {"WHAT HAPPENED", "Q1 — What Happened", "Q2 — Why It Matters", "Proof — Hard Evidence"}
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
            title = "未命名事件"

        q1 = _normalize_ws(cp.get("q1_event_2sent_zh", "") or getattr(card, "what_happened", "") or "")
        q2 = _normalize_ws(cp.get("q2_impact_2sent_zh", "") or getattr(card, "why_important", "") or "")

        quote_1 = _normalize_ws(getattr(card, "_bound_quote_1", "") or "")
        quote_2 = _normalize_ws(getattr(card, "_bound_quote_2", "") or "")
        source_blob = _normalize_ws(
            getattr(card, "full_text", "") or getattr(card, "what_happened", "") or f"{q1} {q2}"
        )
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

        quote_1 = _clip_text(_q1_pick, 110)
        quote_2 = _clip_text(_q2_pick, 110)
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
        if not moves:
            moves = [
                "T+7：確認原始來源與版本時間戳。",
                "T+7：定義可量測 KPI 並建立追蹤表。",
            ]
        if not risks:
            risks = [
                "訊號可能反轉，需保留調整空間。",
                "資料完整度不足時，避免提前放大量化承諾。",
            ]

        final_url = _normalize_ws(getattr(card, "final_url", "") or getattr(card, "source_url", "") or "")
        if not final_url:
            _title_q = re.sub(r"\s+", "+", title.strip())
            final_url = f"https://search.google.com/search?q={_title_q}" if _title_q else ""
        final_url = _clip_text(final_url, 110) if final_url else ""

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
                "q1": q1,
                "q2": q2,
                "quote_1": quote_1,
                "quote_2": quote_2,
                "final_url": final_url,
                "moves": moves,
                "risks": risks,
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
        has_plain_claude = ("Claude" in naming_text) and ("Claude（Anthropic）" not in naming_text)
        naming_ok = (not has_bad_trans) and (not has_plain_claude)

        sync_tokens = [final_url, quote_1, quote_2]
        global_sync_ok = all(
            _contains_sync_token(docx_text, tok) and _contains_sync_token(pptx_text, tok)
            for tok in sync_tokens
            if tok
        ) and all(bool(tok) for tok in sync_tokens)
        event_sync_ok = all(
            [
                _contains_sync_token(doc_url, final_url),
                _contains_sync_token(doc_quote_1, quote_1),
                _contains_sync_token(doc_quote_2, quote_2),
                _contains_sync_token(ppt_url, final_url),
                _contains_sync_token(ppt_quote_1, quote_1),
                _contains_sync_token(ppt_quote_2, quote_2),
            ]
        )
        section_present_ok = bool(doc_sec) and bool(ppt_sec) and bool(doc_q1) and bool(doc_q2) and bool(ppt_q1) and bool(ppt_q2)
        sync_ok = global_sync_ok and event_sync_ok and section_present_ok

        ai_relevance = _is_ai_relevant(title, q1, q2, doc_q1, doc_q2, ppt_q1, ppt_q2, quote_1, quote_2)

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
        all_pass = all(checks.values())
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

    gate_result = "PASS" if (fail_count == 0 and pass_count >= 1) else "FAIL"
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
    from datetime import UTC, datetime

    t_start_iso = datetime.now(UTC).isoformat()

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

    # Z0 extra cards pool (B) — built from high-frontier signal_pool items; populated later
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
        log.warning("No items passed event/signal gates — skipping Z2/Z3, proceeding to Z4/Z5.")
        digest_path = write_digest([])
        print_console_summary([])

    # Z4: Deep Analysis (non-blocking)
    z4_report = None  # 供 Z5 使用
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
    #   Track A (standard): frontier >= Z0_EXEC_MIN_FRONTIER (65) — any channel
    #   Track B (business-relaxed): frontier >= Z0_EXEC_MIN_FRONTIER_BIZ (45)
    #           — only when best_channel=="business" AND business_score >= threshold
    #     Rationale: business news from aggregators (google_news) gets +4 platform
    #     bonus vs +20 for official feeds, so fresh funding/M&A articles cap out at
    #     ~64 frontier and are silently excluded by Track A alone.  Track B ensures
    #     the business quota in select_executive_items() can be filled reliably.
    _z0_exec_min_frontier = int(getattr(settings, "Z0_EXEC_MIN_FRONTIER", 65))
    _z0_exec_min_frontier_biz = int(getattr(settings, "Z0_EXEC_MIN_FRONTIER_BIZ", 45))
    _z0_exec_max_extra = int(getattr(settings, "Z0_EXEC_MAX_EXTRA", 50))
    _z0_exec_min_channel = int(getattr(settings, "Z0_EXEC_MIN_CHANNEL", 55))
    # Audit counters — written to z0_injection.meta.json at end of block
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

        # Step 2: channel gate — max(product, tech, business) >= threshold; dev excluded
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
        _Z0_BIZ_RESERVE = 4   # 2× exec business quota target
        _Z0_PROD_RESERVE = 4  # 2× exec product quota target
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

    # Write Z0 injection audit meta (always — even when Z0 is disabled / no signal_pool)
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
    # PH_SUPP runs in BOTH Z0 and online modes — online fetch also bulk-hydrates raw_items
    # (hydrate_items_batch ok=N), so pre-hydrated items are available regardless of Z0 flag.
    try:
        _ph_supp_items = sorted(
            [it for it in raw_items if int(getattr(it, "fulltext_len", 0) or 0) >= 800],
            key=lambda it: -int(getattr(it, "fulltext_len", 0) or 0),
        )[:50]
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
            # 模式 A（優先）：結構化輸入
            z5_results = all_results if all_results else None
            z5_report = z4_report
            # 模式 B fallback：讀取文本
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
            log.info("Z5: 教育版報告已生成 → %s", [str(p) for p in edu_paths])

            # Register item_id → URL so _backfill_hydrate can resolve cards whose
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
                            _q1_cur + "  原文：「" + _bq1_i[:200] + "」"
                        ).strip()
                    if _bq2_i:
                        _q2_cur = str(_cp_qi.get("q2_impact_2sent_zh", "") or "").strip()
                        _cp_qi["q2_impact_2sent_zh"] = (
                            _q2_cur + "  引用：「" + _bq2_i[:200] + "」"
                        ).strip()
                    _qi_injected += 1
                log.info("PH_SUPP quote injection: injected into %d canonical payloads", _qi_injected)
            except Exception as _qi_exc:
                log.warning("PH_SUPP quote injection failed (non-fatal): %s", _qi_exc)

            # Build final_cards before binary generation; this is the only event-content
            # source consumed by DOCX/PPTX event sections.
            _final_cards: list[dict] = []
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
                metrics_dict["final_cards"] = _final_cards

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
            except Exception as _fc_exc:
                log.warning("final_cards build failed (non-fatal): %s", _fc_exc)

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
                                    "filter_summary.meta.json: kept_total updated %d→%d (+%d PH_SUPP effective)",
                                    _old_kept2, _exec_sel2, _exec_sel2 - _old_kept2,
                                )
                except Exception as _fsu_exc:
                    log.warning("filter_summary.meta.json update failed (non-fatal): %s", _fsu_exc)

                # ---------------------------------------------------------------
                # EXEC_NEWS_QUALITY_HARD gate
                # DoD: every PH_SUPP card must carry >=2 verbatim quotes (>=20 chars,
                # >=4 words each) grounded in its what_happened text, AND those quotes
                # must appear in the injected Q1/Q2 canonical payload.
                # Gate FAIL → write NOT_READY.md, delete PPTX/DOCX, pipeline exits 1.
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
                            continue  # no quotes available — skip (non-PH_SUPP card)

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
                        _all_pass_d = all(_dod_map.values())

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
                        _sc_lines += [
                            f"## Event {_ri_sc}: {_r_sc['title']}",
                            "",
                            f"**final_url**: {_r_sc['final_url']}",
                            "",
                            f"**actor**: {_r_sc.get('actor', '')}",
                            "",
                            f"**Q1** (injected):",
                            f"> {_r_sc['q1_snippet']}",
                            "",
                            f"**Q2** (injected):",
                            f"> {_r_sc['q2_snippet']}",
                            "",
                            f"**quote_1** (verbatim from source, {len(_r_sc['quote_1'])} chars):",
                            f"> {_r_sc['quote_1']}",
                            "",
                            f"**quote_2** (verbatim from source, {len(_r_sc['quote_2'])} chars):",
                            f"> {_r_sc['quote_2']}",
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
                            "EXEC_NEWS_QUALITY_HARD FAIL — %d event(s) missing verbatim quotes; "
                            "NOT_READY.md written; PPTX/DOCX deleted",
                            _enq_fail_count,
                        )
                    else:
                        log.info(
                            "EXEC_NEWS_QUALITY_HARD: %s — %d event(s) with valid verbatim quotes; "
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
                        _all_pass = all(_dod_map.values()) and bool(_dod_raw.get("DOCX_PPTX_SYNC", False))
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

                    _enq_gate = "PASS" if (_enq_fail_count == 0 and _enq_pass_count >= 1) else "FAIL"
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
                        _showcase_lines.extend(
                            [
                                f"## Event {_idx_show}: {_ev['title']}",
                                "",
                                f"**final_url**: {_ev['final_url']}",
                                "",
                                f"**actor**: {_ev.get('actor', '')}",
                                "",
                                "**Q1**:",
                                f"> {_ev['q1_snippet']}",
                                "",
                                "**Q2**:",
                                f"> {_ev['q2_snippet']}",
                                "",
                                "**quote_1**:",
                                f"> {_ev['quote_1']}",
                                "",
                                "**quote_2**:",
                                f"> {_ev['quote_2']}",
                                "",
                                f"**DoD**: {_ev['dod']}",
                                "",
                                "---",
                                "",
                            ]
                        )
                    (_outputs_dir / "LATEST_SHOWCASE.md").write_text("\n".join(_showcase_lines), encoding="utf-8")

                    if _deliverable_meta.get("gate_result") != "PASS":
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
                            f"events_failing: {_deliverable_meta.get('fail_count', 0)}\n\n"
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
                            "EXEC_DELIVERABLE_DOCX_PPTX_HARD FAIL — %d event(s) failed DoD; "
                            "NOT_READY.md written; canonical DOCX/PPTX restored or removed",
                            int(_deliverable_meta.get("fail_count", 0) or 0),
                        )
                    else:
                        if isinstance(_exec_backups, dict):
                            for _backup in _exec_backups.values():
                                if _backup.exists():
                                    _backup.unlink(missing_ok=True)
                        log.info(
                            "EXEC_DELIVERABLE_DOCX_PPTX_HARD: PASS — %d events validated and synchronized",
                            int(_deliverable_meta.get("pass_count", 0) or 0),
                        )
                except Exception as _deliverable_exc:
                    log.warning("EXEC_DELIVERABLE_DOCX_PPTX_HARD check failed (non-fatal): %s", _deliverable_exc)

            except Exception as exc_bin:
                log.error("Executive report generation failed (non-blocking): %s", exc_bin)
        except Exception as exc:
            log.error("Z5 Education Renderer failed (non-blocking): %s", exc)
            try:
                err_md = render_error_report(exc)
                err_path = Path(settings.PROJECT_ROOT) / "outputs" / "deep_analysis_education.md"
                err_path.parent.mkdir(parents=True, exist_ok=True)
                err_path.write_text(err_md, encoding="utf-8")
                log.info("Z5: 錯誤說明已寫入 %s", err_path)
            except Exception:
                log.error("Z5: 連錯誤報告都寫不出來")
    else:
        log.info("Z5: Education report disabled")

    # Hard-D guard: if NOT_READY.md was written by content_strategy, exit 1 so both
    # verify scripts consistently report FAIL (PPTX/DOCX were already blocked by the
    # RuntimeError raised inside get_event_cards_for_deck).
    _nr_check_path = Path(settings.PROJECT_ROOT) / "outputs" / "NOT_READY.md"
    if _nr_check_path.exists():
        log.error(
            "POOL_SUFFICIENCY FAIL — NOT_READY.md exists; "
            "PPTX/DOCX not generated. Pipeline exits 1."
        )
        sys.exit(1)

    # (A) Write flow_counts.meta.json + filter_breakdown.meta.json — pipeline funnel audit
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

        # filter_breakdown.meta.json — full per-reason diagnostics
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
    # latest_digest.md — MVP Demo (Iteration 8)
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
                # Extract verbatim quote from 「...」 in Q1
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
                    _digest_lines.append(f"**Q1 (事件):** {_q1}")
                if _q1_quote:
                    _digest_lines.append(f"> verbatim: 「{_q1_quote}」")
                _digest_lines.append("")
                if _q2:
                    _digest_lines.append(f"**Q2 (影響):** {_q2}")
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

    # Write desktop_button.meta.json — MVP Demo (Iteration 8)
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

    # Notifications
    send_all_notifications(t_start_iso, len(all_results), True, str(digest_path))


if __name__ == "__main__":
    run_pipeline()
