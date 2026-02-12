"""Z2 – AI Core.

Router -> Chain A (extract/summary) -> Chain B (score) -> Chain C (card) -> merge -> gates.

When LLM_PROVIDER=none, uses rule-based fallback for all chains.
"""

from __future__ import annotations

import json
import re
import time

import requests
from config import settings
from schemas.models import MergedResult, RawItem, SchemaA, SchemaB, SchemaC
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
from utils.logger import get_logger
from utils.text_clean import truncate

from core.entity_extraction import extract_entities

# ---------------------------------------------------------------------------
# LLM client (OpenAI-compatible Chat Completions)
# ---------------------------------------------------------------------------


def _llm_available() -> bool:
    return settings.LLM_PROVIDER != "none" and bool(settings.LLM_BASE_URL) and bool(settings.LLM_API_KEY)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type((requests.RequestException, ConnectionError)),
    reraise=True,
)
def _chat_completion(messages: list[dict], temperature: float = 0.3) -> str:
    """Call OpenAI-compatible chat completions endpoint."""
    url = settings.LLM_BASE_URL.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.LLM_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.LLM_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 2048,
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def _parse_json_from_llm(text: str) -> dict:
    """Extract the first JSON object from LLM response text."""
    # Try direct parse
    text = text.strip()
    if text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    # Try to find JSON block
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    # Last resort: find first { ... }
    match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return {}


# ---------------------------------------------------------------------------
# Router (simple source/category mapping)
# ---------------------------------------------------------------------------

_CATEGORY_MAP: dict[str, str] = {
    "tech": "科技/技術",
    "startup": "創業/投融資",
    "ai": "人工智慧",
    "finance": "金融/財經",
    "policy": "政策/監管",
    "security": "資安/網路安全",
    "health": "健康/生醫",
    "climate": "氣候/能源",
    "consumer": "消費電子",
    "gaming": "遊戲/娛樂",
    "general": "綜合資訊",
}

# Keyword-based content classification (checked against title + body)
_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "政策/監管": [
        "regulation",
        "regulatory",
        "legislation",
        "policy",
        "congress",
        "senate",
        "white house",
        "executive order",
        "tariff",
        "sanction",
        "ban",
        "court",
        "ruling",
        "lawsuit",
        "antitrust",
        "compliance",
        "法規",
        "監管",
        "政策",
        "法案",
        "禁令",
        "關稅",
        "制裁",
        "trump",
        "biden",
        "president",
        "governor",
        "fcc",
        "ftc",
        "fda",
        "epa",
        "sec",
        "doj",
    ],
    "資安/網路安全": [
        "security",
        "vulnerability",
        "exploit",
        "breach",
        "hack",
        "malware",
        "ransomware",
        "phishing",
        "zero-day",
        "cve",
        "cybersecurity",
        "encryption",
        "privacy",
        "surveillance",
        "資安",
        "漏洞",
        "駭客",
        "加密",
        "隱私",
    ],
    "健康/生醫": [
        "health",
        "medical",
        "clinical",
        "fda",
        "drug",
        "pharma",
        "biotech",
        "vaccine",
        "therapy",
        "diagnosis",
        "cancer",
        "genome",
        "crispr",
        "hospital",
        "patient",
        "醫療",
        "健康",
        "生醫",
        "藥物",
        "臨床",
        "疫苗",
    ],
    "氣候/能源": [
        "climate",
        "carbon",
        "emission",
        "renewable",
        "solar",
        "wind",
        "battery",
        "ev ",
        "electric vehicle",
        "sustainability",
        "green",
        "nuclear",
        "hydrogen",
        "fossil",
        "energy transition",
        "氣候",
        "碳排",
        "再生能源",
        "電動車",
        "永續",
    ],
    "人工智慧": [
        "artificial intelligence",
        " ai ",
        "machine learning",
        "deep learning",
        "neural network",
        "llm",
        "gpt",
        "transformer",
        "diffusion",
        "chatbot",
        "generative",
        "foundation model",
        "openai",
        "anthropic",
        "人工智慧",
        "機器學習",
        "深度學習",
        "大模型",
        "生成式",
    ],
    "消費電子": [
        "iphone",
        "android",
        "smartphone",
        "tablet",
        "wearable",
        "headset",
        "earbuds",
        "laptop",
        "smartwatch",
        "pixel",
        "galaxy",
        "apple watch",
        "vision pro",
        "手機",
        "穿戴",
        "耳機",
        "平板",
    ],
    "遊戲/娛樂": [
        "gaming",
        "game",
        "playstation",
        "xbox",
        "nintendo",
        "steam",
        "esports",
        "streamer",
        "twitch",
        "epic games",
        "遊戲",
        "電競",
    ],
    "創業/投融資": [
        "startup",
        "funding",
        "series a",
        "series b",
        "seed round",
        "valuation",
        "unicorn",
        "acquisition",
        "ipo",
        "venture",
        "y combinator",
        "techstars",
        "創業",
        "融資",
        "估值",
        "獨角獸",
        "收購",
    ],
}


def classify_content(title: str, body: str, source_category: str = "") -> tuple[str, float]:
    """Classify content by keywords, returning (category_zh, confidence).

    Falls back to source_category mapping if no keyword match.
    """
    text = (title + " " + body[:1000]).lower()

    scores: dict[str, int] = {}
    for cat_zh, keywords in _CATEGORY_KEYWORDS.items():
        hits = sum(1 for kw in keywords if kw in text)
        if hits > 0:
            scores[cat_zh] = hits

    if scores:
        best_cat = max(scores, key=scores.get)  # type: ignore[arg-type]
        best_hits = scores[best_cat]
        confidence = min(1.0, best_hits / 5.0)
        return best_cat, round(confidence, 2)

    # Fallback to source category
    mapped = _CATEGORY_MAP.get(source_category, "綜合資訊")
    return mapped, 0.3


def route_item(item: RawItem) -> str:
    """Map item to Traditional Chinese category label using content analysis."""
    cat, _ = classify_content(item.title, item.body, item.source_category)
    return cat


# ---------------------------------------------------------------------------
# Chain A – Extract & Summary (LLM)
# ---------------------------------------------------------------------------

_CHAIN_A_PROMPT = """你是一位專業的資訊分析助手。請從以下新聞中提取關鍵資訊，並生成繁體中文摘要。

標題: {title}
來源: {source} ({category})
內容: {body}

請以嚴格的 JSON 格式回傳，不要包含其他文字:
{{
  "item_id": "{item_id}",
  "title_zh": "繁體中文標題",
  "summary_zh": "100-200字的繁體中文摘要",
  "category": "分類",
  "entities": ["實體1", "實體2"],
  "key_points": ["要點1", "要點2", "要點3"],
  "source_id": "{source}",
  "score_seed": 0
}}"""


def chain_a_llm(item: RawItem) -> SchemaA:
    """Chain A via LLM."""
    prompt = _CHAIN_A_PROMPT.format(
        title=item.title,
        source=item.source_name,
        category=route_item(item),
        body=truncate(item.body, 3000),
        item_id=item.item_id,
    )
    raw = _chat_completion([{"role": "user", "content": prompt}])
    d = _parse_json_from_llm(raw)
    d["item_id"] = item.item_id
    d["source_id"] = item.source_name
    return SchemaA.from_dict(d)


# ---------------------------------------------------------------------------
# Chain A – Fallback (rule-based)
# ---------------------------------------------------------------------------


def chain_a_fallback(item: RawItem) -> SchemaA:
    """Rule-based extraction when LLM is unavailable."""
    body = item.body

    # Summary: first 200 chars
    summary = body[:200] + ("..." if len(body) > 200 else "")

    # Entity extraction via the new pipeline
    result = extract_entities(
        title=item.title,
        body=body,
        url=item.url,
        lang=item.lang,
        max_entities=8,
    )
    entities = result.top_entity_strings

    # Key points: first 3 substantial sentences
    sentences = re.split(r"[.。!！?？;；\n]", body)
    key_points = [s.strip() for s in sentences if len(s.strip()) > 15][:3]

    # Content-based classification
    category, _confidence = classify_content(item.title, body, item.source_category)

    return SchemaA(
        item_id=item.item_id,
        title_zh=item.title,
        summary_zh=summary,
        category=category,
        entities=entities,
        key_points=key_points,
        source_id=item.source_name,
        score_seed=0,
    )


# ---------------------------------------------------------------------------
# Chain B – Scoring (LLM)
# ---------------------------------------------------------------------------

_CHAIN_B_PROMPT = """你是一位新聞品質評分專家。請對以下資訊進行評分。

標題: {title}
摘要: {summary}
來源: {source}

評分維度 (1-10):
- novelty: 新穎度
- utility: 實用性
- heat: 熱度
- feasibility: 可行性

同時判斷:
- dup_risk: 重複風險 (0-1)
- is_ad: 是否為廣告
- tags: 相關標籤

請以嚴格的 JSON 格式回傳:
{{
  "item_id": "{item_id}",
  "novelty": 0,
  "utility": 0,
  "heat": 0,
  "feasibility": 0,
  "final_score": 0,
  "dup_risk": 0,
  "is_ad": false,
  "tags": ["tag1"]
}}"""


def chain_b_llm(item: RawItem, schema_a: SchemaA) -> SchemaB:
    """Chain B via LLM."""
    prompt = _CHAIN_B_PROMPT.format(
        title=schema_a.title_zh or item.title,
        summary=schema_a.summary_zh,
        source=item.source_name,
        item_id=item.item_id,
    )
    raw = _chat_completion([{"role": "user", "content": prompt}])
    d = _parse_json_from_llm(raw)
    d["item_id"] = item.item_id
    # Ensure final_score is computed
    if not d.get("final_score"):
        scores = [float(d.get(k, 5)) for k in ("novelty", "utility", "heat", "feasibility")]
        d["final_score"] = round(sum(scores) / len(scores), 2)
    return SchemaB.from_dict(d)


# ---------------------------------------------------------------------------
# Chain B – Fallback (rule-based scoring heuristics)
# ---------------------------------------------------------------------------

_AD_KEYWORDS = {
    "sponsored",
    "advertisement",
    "广告",
    "推广",
    "优惠",
    "折扣",
    "coupon",
    "promo",
    "click here",
    "limited time",
    "免费领取",
    "buy now",
    "立即购买",
    "exclusive deal",
}


def chain_b_fallback(item: RawItem, schema_a: SchemaA) -> SchemaB:
    """Heuristic scoring when LLM is unavailable."""
    body_lower = (item.body + " " + item.title).lower()

    # Ad detection
    ad_hits = sum(1 for kw in _AD_KEYWORDS if kw in body_lower)
    is_ad = ad_hits >= 2

    # Novelty: longer body and more entities = higher novelty
    body_len = len(item.body)
    novelty = min(10, 3 + (body_len / 500) + len(schema_a.entities) * 0.5)

    # Utility: presence of key points and concrete entities
    utility = min(10, 4 + len(schema_a.key_points) * 1.5 + len(schema_a.entities) * 0.5)

    # Heat: based on source reputation
    source_heat = {"36kr": 7, "HackerNews": 8, "TechCrunch": 7}.get(item.source_name, 5)
    heat = min(10, source_heat + (body_len / 1000))

    # Feasibility: higher for tech/startup categories
    feasibility_map = {"tech": 7, "startup": 6, "ai": 8, "finance": 5}
    feasibility = feasibility_map.get(item.source_category, 5)

    novelty = round(min(novelty, 10), 1)
    utility = round(min(utility, 10), 1)
    heat = round(min(heat, 10), 1)
    feasibility = round(min(feasibility, 10), 1)

    final_score = round((novelty + utility + heat + feasibility) / 4, 2)
    dup_risk = 0.0  # already deduped in Z1

    # Tags from category + source
    tags = [item.source_category, item.source_name]
    if is_ad:
        tags.append("ad")

    return SchemaB(
        item_id=item.item_id,
        novelty=novelty,
        utility=utility,
        heat=heat,
        feasibility=feasibility,
        final_score=final_score,
        dup_risk=dup_risk,
        is_ad=is_ad,
        tags=tags,
    )


# ---------------------------------------------------------------------------
# Chain C – Feishu Card (LLM)
# ---------------------------------------------------------------------------

_CHAIN_C_PROMPT = """根據以下資訊生成飛書卡片訊息的 Markdown 內容。

標題: {title}
摘要: {summary}
分數: {score}
來源: {source}
連結: {url}

請以嚴格的 JSON 格式回傳:
{{
  "item_id": "{item_id}",
  "card_md": "飛書 Markdown 格式的卡片內容",
  "title": "卡片標題",
  "brief": "30字內簡述",
  "cta_url": "{url}"
}}"""


def chain_c_llm(item: RawItem, schema_a: SchemaA, schema_b: SchemaB) -> SchemaC:
    """Chain C via LLM."""
    prompt = _CHAIN_C_PROMPT.format(
        title=schema_a.title_zh or item.title,
        summary=schema_a.summary_zh,
        score=schema_b.final_score,
        source=item.source_name,
        url=item.url,
        item_id=item.item_id,
    )
    raw = _chat_completion([{"role": "user", "content": prompt}])
    d = _parse_json_from_llm(raw)
    d["item_id"] = item.item_id
    d["cta_url"] = item.url
    return SchemaC.from_dict(d)


# ---------------------------------------------------------------------------
# Chain C – Fallback (rule-based card generation)
# ---------------------------------------------------------------------------


def chain_c_fallback(item: RawItem, schema_a: SchemaA, schema_b: SchemaB) -> SchemaC:
    """Generate Feishu card markdown without LLM."""
    title = schema_a.title_zh or item.title
    summary = schema_a.summary_zh or item.body[:150]
    brief = summary[:30] + "..." if len(summary) > 30 else summary

    score_str = f"{schema_b.final_score:.1f}"
    tags_str = " ".join(f"#{t}" for t in schema_b.tags)

    card_md = (
        f"**{title}**\n\n"
        f"{summary}\n\n"
        f"---\n"
        f"評分: {score_str} | 來源: {item.source_name}\n"
        f"標籤: {tags_str}\n\n"
        f"[查看原文]({item.url})"
    )

    return SchemaC(
        item_id=item.item_id,
        card_md=card_md,
        title=title,
        brief=brief,
        cta_url=item.url,
    )


# ---------------------------------------------------------------------------
# Pipeline: process a single item through A -> B -> C
# ---------------------------------------------------------------------------


def process_item(item: RawItem) -> MergedResult:
    """Run all three chains on a single item, with LLM or fallback."""
    log = get_logger()
    use_llm = _llm_available()

    t0 = time.time()
    try:
        # Chain A
        if use_llm:
            try:
                schema_a = chain_a_llm(item)
            except Exception as exc:
                log.warning("Chain A LLM failed for %s, using fallback: %s", item.item_id, exc)
                schema_a = chain_a_fallback(item)
        else:
            schema_a = chain_a_fallback(item)

        # Chain B
        if use_llm:
            try:
                schema_b = chain_b_llm(item, schema_a)
            except Exception as exc:
                log.warning("Chain B LLM failed for %s, using fallback: %s", item.item_id, exc)
                schema_b = chain_b_fallback(item, schema_a)
        else:
            schema_b = chain_b_fallback(item, schema_a)

        # Chain C
        if use_llm:
            try:
                schema_c = chain_c_llm(item, schema_a, schema_b)
            except Exception as exc:
                log.warning("Chain C LLM failed for %s, using fallback: %s", item.item_id, exc)
                schema_c = chain_c_fallback(item, schema_a, schema_b)
        else:
            schema_c = chain_c_fallback(item, schema_a, schema_b)

        # Quality gates
        passed = (
            schema_b.final_score >= settings.GATE_MIN_SCORE
            and schema_b.dup_risk <= settings.GATE_MAX_DUP_RISK
            and not schema_b.is_ad
        )

        elapsed = time.time() - t0
        log.info(
            "Processed %s | score=%.2f dup=%.2f ad=%s gate=%s | %.2fs",
            item.item_id,
            schema_b.final_score,
            schema_b.dup_risk,
            schema_b.is_ad,
            passed,
            elapsed,
        )

        return MergedResult(
            item_id=item.item_id,
            schema_a=schema_a,
            schema_b=schema_b,
            schema_c=schema_c,
            passed_gate=passed,
        )

    except Exception as exc:
        log.error("Failed to process item %s: %s", item.item_id, exc)
        # Return a minimal result so the batch continues
        return MergedResult(
            item_id=item.item_id,
            schema_a=SchemaA(item_id=item.item_id),
            schema_b=SchemaB(item_id=item.item_id),
            schema_c=SchemaC(item_id=item.item_id),
            passed_gate=False,
        )


def process_batch(items: list[RawItem]) -> list[MergedResult]:
    """Process a batch of items. Per-item failure does not stop the batch."""
    results: list[MergedResult] = []
    for item in items:
        results.append(process_item(item))
    return results
