"""Topic router: classify content into Product/Tech/Business/Dev channels
and gate irrelevant non-AI content (e.g. building/real-estate noise).

All logic is regex + keyword only — stdlib only, no network calls, no API keys.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Company / model whitelist — fast-pass relevance
# ---------------------------------------------------------------------------

_COMPANY_WHITELIST_RE = re.compile(
    r"\b(?:OpenAI|Anthropic|Google|DeepMind|NVIDIA|Meta|Microsoft|AWS|Amazon|"
    r"Intel|xAI|Mistral|Cohere|Inflection|HuggingFace|Hugging\s+Face|"
    r"DeepSeek|Qwen|Alibaba|Baidu|Tencent|ByteDance|Samsung|Apple|Tesla|"
    r"Palantir|Scale\s*AI|Databricks|Snowflake|Groq|Together\s*AI|Perplexity|"
    r"Character\.?AI|Runway|Midjourney|ElevenLabs|Suno|Kling|Replicate|"
    r"Cohere|Adept|AI21|01\.AI|Moonshot|MiniMax|Zhipu|Baichuan|"
    r"Falcon|TII|LAION|EleutherAI|Stability\s*AI|Ideogram|Pika|Luma)\b",
    re.IGNORECASE,
)

_MODEL_WHITELIST_RE = re.compile(
    r"\b(?:GPT-?[0-9o]+(?:-?mini|-?turbo|-?preview|-?pro)?|"
    r"Claude(?:-?\d+(?:\.\d+)?)?|"
    r"Gemini(?:-?\d+(?:\.\d+)?)?|"
    r"Llama-?[0-9]+|LLaMA-?[0-9]+|"
    r"Mistral(?:-\w+)?|Mixtral(?:-\w+)?|"
    r"Grok-?[0-9]?|DeepSeek-?(?:R[0-9]+|V[0-9]+|Coder|Chat)?|"
    r"Qwen-?[0-9]|Command-?[RX]?|Phi-?\d+|"
    r"Falcon-?\d+|Gemma-?\d+|DALL-?E\s*[23]?|Sora|"
    r"Stable\s*Diffusion|SD\s*[0-9XL]+|Copilot|Codex|Whisper|"
    r"PaLM-?[0-9]?|Veo-?[0-9]?|o[123](?:-mini|-preview)?|o3-mini)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# AI core keywords — split English (word-bounded) + Chinese (no boundary)
# ---------------------------------------------------------------------------

_AI_CORE_EN_RE = re.compile(
    r"\b(?:AI|A\.I\.|LLM|LLMs|GPT|AGI|"
    r"model|models|inference|agent|agents|weights|benchmark|benchmarks|"
    r"machine\s+learning|ML|deep\s+learning|neural\s+net|"
    r"transformer|foundation\s+model|large\s+language|"
    r"generative|gen\s*ai|gen-?ai)\b",
    re.IGNORECASE,
)

_AI_CORE_ZH_RE = re.compile(
    r"人工智慧|機器學習|深度學習|神經網路|大語言模型|生成式|語言模型|基礎模型|推理|訓練"
)

# ---------------------------------------------------------------------------
# Hard negative: building / real estate
# English: word-bounded; Chinese: no boundary needed
# ---------------------------------------------------------------------------

_BUILDING_EN_RE = re.compile(
    r"\b(?:building|buildings|construction|real\s+estate|property\s+developer|"
    r"apartment|condo|condominium|residential\s+(?:tower|complex|unit|building)|"
    r"skyscraper|commercial\s+real\s+estate|housing\s+(?:project|development)|"
    r"real\s+estate\s+developer)\b",
    re.IGNORECASE,
)

_BUILDING_ZH_RE = re.compile(
    r"大樓|建案|建築物|住宅(?:大樓|區|社區)|房產|房地產|地產|商業地產|商辦大樓|"
    r"都更|樓地板|建設公司|建商|土地開發|售屋|買屋|租屋|房仲|豪宅|透天厝"
)

# ---------------------------------------------------------------------------
# Channel keyword patterns
# English: word-bounded; Chinese: no boundary needed (written together)
# ---------------------------------------------------------------------------

_PRODUCT_EN_RE = re.compile(
    r"\b(?:launch(?:es|ed|ing)?|GA|generally\s+available|"
    r"beta|preview|alpha|early\s+access|"
    r"releas(?:e[sd]?|ing)|announc(?:e[sd]?|ing|ement)|"
    r"pricing|price|tier|plan|subscription|"
    r"availab(?:le|ility)|feature|update|upgrade|"
    r"v\d+\.\d+(?:\.\d+)?|version\s+\d|rolled?\s*out|ship(?:s|ped|ping)?|"
    r"new\s+model|new\s+product|product\s+update|API\s+update)\b",
    re.IGNORECASE,
)
_PRODUCT_ZH_RE = re.compile(
    r"新功能|上線|發布|發佈|推出|正式版|開放|定價|方案|訂閱|更新|升級|測試版|搶先版|新版本"
)

_TECH_EN_RE = re.compile(
    r"\b(?:weights|checkpoint|benchmark|arXiv|arxiv|"
    r"architecture|inference|quantiz(?:e|ation)|"
    r"system\s+card|throughput|latency|"
    r"fine-?tun(?:e|ing|ed)|RLHF|RLAIF|DPO|"
    r"token(?:s|ization)?|context\s+window|"
    r"MMLU|GPQA|HumanEval|GSM8K|SWE-?bench|AIME|LiveBench|"
    r"parameter(?:s)?|LoRA|RAG|embedding|attention|"
    r"diffusion|multimodal|vision\s+model|audio\s+model|"
    r"TTS|STT|ASR|MoE|mixture\s+of\s+experts)\b",
    re.IGNORECASE,
)
_TECH_ZH_RE = re.compile(
    r"權重|量化|微調|基準|效能|延遲|參數|推論|多模態"
)

_BUSINESS_EN_RE = re.compile(
    r"\b(?:fund(?:ing|ed|raise[sd]?)?|Series\s+[A-E]\b|seed\s+(?:round|funding)|"
    r"acqui(?:r(?:e[sd]?|ing|ition)|sition)|"
    r"merg(?:e[sd]?|er|ing)|"
    r"partnership|partner(?:s|ing|ed)|"
    r"contract|customer(?:s)?|enterprise|"
    r"ARR|MRR|revenue|valuation|IPO|SPAC|"
    r"deal|investment|invest(?:ed|ing|or)|"
    r"expand(?:ing|ed|s)?|expansion|"
    r"layoff|hire[sd]?|hiring|"
    r"CEO|CTO|CFO|COO|founder|co-?founder)\b",
    re.IGNORECASE,
)
_BUSINESS_ZH_RE = re.compile(
    r"融資|募資|收購|併購|合作|合夥|客戶|企業|營收|估值|上市|裁員|招募|擴張|策略|商業模式|投資"
)

_DEV_EN_RE = re.compile(
    r"\b(?:GitHub|open-?source[d]?|open\s+weight[s]?|open\s+source[d]?|"
    r"repo(?:sitory)?|pull\s+request|"
    r"changelog|release\s+note|"
    r"librar(?:y|ies)|framework|SDK|CLI|"
    r"plugin|extension|package|npm|pip|pypi|"
    r"Hugging\s*Face\s+Hub|HF\s+Hub|"
    r"star(?:s|red|ring)?|fork(?:s|ed)?|"
    r"breaking\s+change|deprecat(?:e[sd]?|ion))\b",
    re.IGNORECASE,
)
_DEV_ZH_RE = re.compile(
    r"開源|框架|函式庫|套件|工具"
)

# Number/amount patterns that boost confidence
_NUMBER_BOOST_RE = re.compile(
    r"\$[\d,.]+\s*[BMK]?B?\b"
    r"|£[\d,.]+[BMK]?\b"
    r"|\d+(?:\.\d+)?\s*[BMK]\s*(?:parameters?|tokens?|users?|downloads?)?"
    r"|\d+(?:\.\d+)?\s*%"
    r"|v\d+\.\d+(?:\.\d+)?"
    r"|\b\d{4}\b"
    r"|\b(?:billion|million|thousand)\b",
    re.IGNORECASE,
)


def _count_hits(text: str, en_re: re.Pattern, zh_re: re.Pattern) -> int:
    """Count total keyword hits across English and Chinese patterns."""
    return len(en_re.findall(text)) + len(zh_re.findall(text))


def _has_ai_core(text: str) -> bool:
    return bool(_AI_CORE_EN_RE.search(text)) or bool(_AI_CORE_ZH_RE.search(text))


def _has_building(text: str) -> bool:
    return bool(_BUILDING_EN_RE.search(text)) or bool(_BUILDING_ZH_RE.search(text))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_channels(
    text: str,
    url: str = "",
    domain: str = "",
    source_platform: str = "",
) -> dict:
    """Score content across 4 channels (Product / Tech / Business / Dev).

    Returns dict with:
        product_score, tech_score, business_score, dev_score  (int 0-100)
        best_channel  (str: "product" | "tech" | "business" | "dev")
        reasons       (list[str])

    best_channel is determined by raw hit count (before capping) so that
    domain-specific content isn't over-shadowed by generic product keywords.
    """
    full = f"{text} {url} {source_platform}"

    # Raw hit counts (used for best_channel tiebreaking)
    raw: dict[str, int] = {
        "product": _count_hits(full, _PRODUCT_EN_RE, _PRODUCT_ZH_RE),
        "tech":    _count_hits(full, _TECH_EN_RE,    _TECH_ZH_RE),
        "business":_count_hits(full, _BUSINESS_EN_RE,_BUSINESS_ZH_RE),
        "dev":     _count_hits(full, _DEV_EN_RE,     _DEV_ZH_RE),
    }

    # URL-domain boosts (added as raw count increments to preserve tiebreaker)
    url_lower = url.lower()
    if "arxiv.org" in url_lower:
        raw["tech"] += 3        # strong tech signal
    if "github.com" in url_lower:
        raw["dev"] += 3         # strong dev signal
    if "huggingface.co" in url_lower:
        raw["dev"] += 2
        raw["tech"] += 1

    # best_channel determined by raw counts (most domain-relevant)
    best_channel = max(raw, key=lambda k: raw[k])

    # Convert to 0-100 scores with number boost
    num_boost = 20 if _NUMBER_BOOST_RE.search(full) else 0

    def _score(hits: int) -> int:
        if hits == 0:
            return 0
        return min(hits * 25 + num_boost, 100)

    p_score = _score(raw["product"])
    t_score = _score(raw["tech"])
    b_score = _score(raw["business"])
    d_score = _score(raw["dev"])

    reasons = [f"{ch}={raw[ch]}" for ch in raw if raw[ch] > 0]
    if not reasons:
        reasons = ["no_channel_signal"]

    return {
        "product_score": p_score,
        "tech_score":    t_score,
        "business_score":b_score,
        "dev_score":     d_score,
        "best_channel":  best_channel,
        "reasons":       reasons,
    }


def is_relevant_ai(
    text: str,
    url: str = "",
    domain: str = "",
) -> tuple[bool, list[str]]:
    """Relevance gate: is this content appropriate for the executive AI report?

    Returns (is_relevant: bool, reasons: list[str]).

    PASS if any of:
      1) Known company / model name (whitelist hit)
      2) Any channel_score >= 35 AND AI core keyword present
      3) AI core keyword present (low-score content still passes)

    REJECT if:
      - Building / real-estate hit AND no AI core keyword  (hard negative)
    """
    full_text = f"{text} {url}"

    ai_core_hit = _has_ai_core(full_text)
    building_hit = _has_building(full_text)

    # Hard negative: building noise without any AI content
    if building_hit and not ai_core_hit:
        return False, ["hard_neg:building_real_estate_no_ai_core"]

    # Whitelist fast-pass
    if _COMPANY_WHITELIST_RE.search(full_text) or _MODEL_WHITELIST_RE.search(full_text):
        return True, ["whitelist:company_or_model"]

    # Channel score check
    ch = classify_channels(text, url, domain, "")
    max_score = max(
        ch["product_score"],
        ch["tech_score"],
        ch["business_score"],
        ch["dev_score"],
    )

    if max_score >= 35 and ai_core_hit:
        return True, [f"channel_score={max_score},ai_core=True"]

    # AI core present even if channel signal is weak — still passes
    if ai_core_hit:
        return True, [f"ai_core_present,channel_score={max_score}"]

    return False, [f"no_ai_core,max_channel_score={max_score}"]
