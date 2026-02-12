"""Z4 – Deep Analyzer.

Transforms MergedResult items that passed quality gates into
an engineering/investment/strategy-grade intelligence report with:
- Per-item evidence-driven deep dives (not boilerplate)
- Cross-news meta analysis (executive signals, themes, opportunities, actionable signals)

Key changes from v1:
- Event breakdown uses core_facts extracted from text + evidence_excerpts
- First principles selects ONE mechanism from a controlled list
- Second-order effects split into derivable vs speculative
- Opportunities tied to mechanism + stakeholder (max 3)
- Strategic outlook includes measurable observation_metrics + counter_risks
- Signal strength incorporates evidence_density

Uses LLM when available, falls back to evidence-driven heuristics.
Errors are logged but never kill the pipeline.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime

from schemas.models import DeepAnalysisReport, ItemDeepDive, MergedResult
from utils.logger import get_logger

from core.ai_core import _chat_completion, _llm_available, _parse_json_from_llm

# ---------------------------------------------------------------------------
# Controlled mechanism list for first-principles analysis
# ---------------------------------------------------------------------------

MECHANISMS: list[str] = [
    "可擴展性（scalability）",
    "延遲／效能（latency）",
    "隱私保護（privacy）",
    "互操作性（interoperability）",
    "監管槓桿（regulatory leverage）",
    "激勵設計（incentive design）",
    "供應鏈（supply chain）",
    "安全邊界（security boundary）",
    "採用曲線（adoption curve）",
]

# Keywords that hint at which mechanism is relevant
_MECHANISM_KEYWORDS: dict[str, list[str]] = {
    "可擴展性（scalability）": [
        "scale",
        "scaling",
        "growth",
        "expand",
        "million",
        "billion",
        "users",
        "distributed",
        "cluster",
        "parallel",
        "throughput",
        "capacity",
        "擴展",
        "規模",
        "分散式",
    ],
    "延遲／效能（latency）": [
        "fast",
        "speed",
        "latency",
        "performance",
        "real-time",
        "optimize",
        "benchmark",
        "inference",
        "training",
        "compute",
        "efficiency",
        "效能",
        "延遲",
        "加速",
        "優化",
    ],
    "隱私保護（privacy）": [
        "privacy",
        "data protection",
        "gdpr",
        "consent",
        "surveillance",
        "tracking",
        "personal data",
        "anonymi",
        "隱私",
        "個資",
    ],
    "互操作性（interoperability）": [
        "interop",
        "compatible",
        "integration",
        "api",
        "standard",
        "protocol",
        "framework",
        "sdk",
        "plugin",
        "ecosystem",
        "deploy",
        "互操作",
        "整合",
        "標準",
        "框架",
    ],
    "監管槓桿（regulatory leverage）": [
        "regulation",
        "compliance",
        "policy",
        "law",
        "ban",
        "tariff",
        "sanction",
        "approval",
        "fda",
        "fcc",
        "approved",
        "clinical trial",
        "efficacy",
        "patent",
        "license",
        "監管",
        "法規",
        "合規",
        "審批",
        "核准",
    ],
    "激勵設計（incentive design）": [
        "incentive",
        "reward",
        "pricing",
        "subscription",
        "monetiz",
        "freemium",
        "valuation",
        "funding",
        "revenue",
        "business model",
        "ipo",
        "激勵",
        "定價",
        "商業模式",
        "營收",
        "估值",
        "融資",
    ],
    "供應鏈（supply chain）": [
        "supply chain",
        "manufacture",
        "chip",
        "semiconductor",
        "shortage",
        "logistics",
        "factory",
        "production",
        "gigafactory",
        "battery",
        "cell",
        "供應鏈",
        "晶片",
        "製造",
        "工廠",
        "產能",
    ],
    "安全邊界（security boundary）": [
        "security",
        "vulnerability",
        "breach",
        "attack",
        "encrypt",
        "zero-day",
        "patch",
        "exploit",
        "malware",
        "cve",
        "kernel",
        "資安",
        "漏洞",
        "攻擊",
        "修補",
    ],
    "採用曲線（adoption curve）": [
        "adopt",
        "mainstream",
        "early adopter",
        "launch",
        "release",
        "rollout",
        "推出",
        "採用",
        "上線",
    ],
}

# Stakeholder inference from entities and category
_STAKEHOLDER_TEMPLATES: dict[str, list[tuple[str, str]]] = {
    "科技/技術": [
        ("技術開發者", "希望降低開發成本、提升工具品質"),
        ("平台營運方", "追求用戶成長與生態鎖定"),
        ("終端使用者", "期望更好的體驗與更低的使用門檻"),
    ],
    "創業/投融資": [
        ("創始團隊", "追求產品市場契合與估值成長"),
        ("投資機構", "尋求風險調整後回報最大化"),
        ("目標使用者", "期望痛點被解決、價格合理"),
    ],
    "人工智慧": [
        ("AI 研究團隊", "追求模型效能突破與論文影響力"),
        ("應用開發者", "希望降低 AI 整合門檻"),
        ("終端使用者", "期望可靠、安全、易用的 AI 功能"),
    ],
    "政策/監管": [
        ("立法／監管機構", "追求公共利益保護與市場秩序"),
        ("被監管企業", "希望合規成本最小化、維持營運彈性"),
        ("消費者／公民", "期望權益受保護、市場公平競爭"),
    ],
    "資安/網路安全": [
        ("安全研究者", "追求漏洞披露與防禦技術推進"),
        ("企業安全團隊", "需要即時修補與風險控制"),
        ("攻擊方", "尋求利用窗口與經濟收益"),
    ],
    "健康/生醫": [
        ("研究機構／藥廠", "追求臨床突破與商業化"),
        ("醫療體系", "需要有效、可負擔的治療方案"),
        ("患者", "期望療效改善與可及性提升"),
    ],
    "氣候/能源": [
        ("能源企業", "在轉型壓力下尋求新利潤來源"),
        ("政府／監管", "推動減碳目標與能源安全"),
        ("消費者", "期望更低成本與更永續的能源選擇"),
    ],
}


# ---------------------------------------------------------------------------
# Helpers: extract evidence from text
# ---------------------------------------------------------------------------


def _extract_sentences(text: str, max_count: int = 10) -> list[str]:
    """Split text into sentences, return up to max_count non-trivial ones."""
    parts = re.split(r"[.。!！?？;；\n]+", text)
    sentences = [s.strip() for s in parts if len(s.strip()) > 15]
    return sentences[:max_count]


def _extract_evidence_excerpts(text: str, max_excerpts: int = 2, max_words: int = 25) -> list[str]:
    """Extract short evidence excerpts (<= max_words words) from text."""
    sentences = _extract_sentences(text, max_count=6)
    excerpts: list[str] = []
    for s in sentences:
        words = s.split()
        if len(words) > max_words:
            s = " ".join(words[:max_words]) + "..."
        if s and s not in excerpts:
            excerpts.append(s)
        if len(excerpts) >= max_excerpts:
            break
    return excerpts


def _select_mechanism(title: str, body: str) -> str:
    """Select the most relevant mechanism from the controlled list."""
    text = (title + " " + body[:1000]).lower()
    scores: dict[str, int] = {}
    for mechanism, keywords in _MECHANISM_KEYWORDS.items():
        hits = sum(1 for kw in keywords if kw in text)
        if hits > 0:
            scores[mechanism] = hits

    if scores:
        return max(scores, key=scores.get)  # type: ignore[arg-type]

    # Default: adoption curve (most generic)
    return "採用曲線（adoption curve）"


def _get_stakeholders(category: str, entities: list[str]) -> list[tuple[str, str, str]]:
    """Get stakeholders with motivations and constraints.

    Returns list of (stakeholder_name, motivation, constraint).
    """
    templates = _STAKEHOLDER_TEMPLATES.get(category, _STAKEHOLDER_TEMPLATES.get("科技/技術", []))

    result: list[tuple[str, str, str]] = []
    for name, motivation in templates[:3]:
        # Try to link stakeholder to a specific entity
        entity_ref = ""
        if entities:
            # Simple heuristic: first entity for first stakeholder, etc.
            idx = len(result) % len(entities)
            entity_ref = entities[idx]

        # Generate a constraint based on stakeholder type
        constraints = {
            "技術開發者": "技術債務與維護成本",
            "平台營運方": "用戶獲取成本與監管壓力",
            "終端使用者": "學習曲線與遷移成本",
            "創始團隊": "資金跑道與團隊擴張壓力",
            "投資機構": "投資組合風險分散要求",
            "目標使用者": "替代方案的轉換成本",
            "AI 研究團隊": "計算資源限制與可重現性",
            "應用開發者": "整合複雜度與 API 穩定性",
            "立法／監管機構": "執行資源與跨國協調難度",
            "被監管企業": "合規成本與競爭力維持",
            "消費者／公民": "資訊不對稱與參與成本",
            "安全研究者": "漏洞披露時序與倫理考量",
            "企業安全團隊": "修補速度與業務連續性平衡",
            "攻擊方": "防禦技術演進與法律風險",
            "研究機構／藥廠": "臨床試驗耗時與審批不確定性",
            "醫療體系": "預算限制與系統整合",
            "患者": "可及性與經濟負擔",
            "能源企業": "轉型投資與股東期望",
            "政府／監管": "政治可行性與國際協定",
            "消費者": "價格敏感度與行為慣性",
        }
        constraint = constraints.get(name, "預算與資源限制")

        if entity_ref:
            result.append((f"{name}（如 {entity_ref}）", motivation, constraint))
        else:
            result.append((name, motivation, constraint))

    return result


# ---------------------------------------------------------------------------
# Signal strength (improved with evidence density)
# ---------------------------------------------------------------------------


def _compute_signal_strength(b, evidence_density: float = 0.5) -> float:
    """Signal strength = weighted sum of novelty, impact, feasibility, heat, evidence_density.

    evidence_density = (# core facts with evidence) / (total core facts).
    """
    raw = (
        b.novelty * 0.2
        + b.utility * 0.15
        + b.heat * 0.15
        + b.feasibility * 0.15
        + b.final_score * 0.15
        + evidence_density * 10.0 * 0.2  # scale 0-1 to 0-10
    )
    return round(min(10.0, raw), 2)


# ---------------------------------------------------------------------------
# Per-item analysis: LLM path (improved prompts)
# ---------------------------------------------------------------------------

_ITEM_DEEP_DIVE_PROMPT = """你是一位頂級戰略情報分析師。請對以下新聞進行深度分析。

標題: {title}
摘要: {summary}
分類: {category}
實體: {entities}
要點: {key_points}

**嚴格要求**：
1. 「核心事實」必須是從原文直接提取或輕度改寫的事實，不可包含預測或推論。
2. 「證據片段」引用原文中支持核心事實的短句（每段 <= 25 字）。
3. 「可直接推導的影響」只包含從事實可機械推導的短期結果。
4. 「需驗證的推測」必須明確標註為假說並附帶驗證信號。
5. 從以下機制列表中選擇最相關的 ONE 機制：{mechanisms}

請以嚴格的 JSON 格式回傳:
{{
  "core_facts": ["事實1", "事實2"],
  "evidence_excerpts": ["引文1", "引文2"],
  "forces_incentives": "力場分析（含具體利益相關方、動機、約束）",
  "first_principles_mechanism": "所選機制名稱",
  "first_principles": "基於所選機制的分析",
  "derivable_effects": ["可推導影響1", "可推導影響2"],
  "speculative_effects": ["推測1（驗證信號：XXX）"],
  "opportunities": ["機會1", "機會2"],
  "observation_metrics": ["指標1", "指標2", "指標3"],
  "counter_risks": ["風險1"],
  "strategic_outlook_3y": "展望"
}}"""


def _analyze_item_llm(r: MergedResult) -> ItemDeepDive:
    """Analyze a single item via LLM with improved prompts."""
    a, b = r.schema_a, r.schema_b
    prompt = _ITEM_DEEP_DIVE_PROMPT.format(
        title=a.title_zh or "（無標題）",
        summary=a.summary_zh or "（無摘要）",
        category=a.category or "綜合資訊",
        entities=", ".join(a.entities) if a.entities else "無",
        key_points="; ".join(a.key_points) if a.key_points else "無",
        mechanisms=", ".join(MECHANISMS),
    )
    raw = _chat_completion([{"role": "user", "content": prompt}], temperature=0.4)
    d = _parse_json_from_llm(raw)

    core_facts = d.get("core_facts", [])
    if isinstance(core_facts, str):
        core_facts = [core_facts]
    evidence = d.get("evidence_excerpts", [])
    if isinstance(evidence, str):
        evidence = [evidence]

    opps = d.get("opportunities", [])
    if isinstance(opps, str):
        opps = [opps]

    derivable = d.get("derivable_effects", [])
    if isinstance(derivable, str):
        derivable = [derivable]
    speculative = d.get("speculative_effects", [])
    if isinstance(speculative, str):
        speculative = [speculative]

    obs = d.get("observation_metrics", [])
    if isinstance(obs, str):
        obs = [obs]
    risks = d.get("counter_risks", [])
    if isinstance(risks, str):
        risks = [risks]

    # Evidence density
    ev_density = len(evidence) / max(len(core_facts), 1)
    signal = _compute_signal_strength(b, ev_density)

    return ItemDeepDive(
        item_id=r.item_id,
        core_facts=[str(f) for f in core_facts],
        evidence_excerpts=[str(e) for e in evidence],
        event_breakdown=str(d.get("event_breakdown", "")),
        forces_incentives=str(d.get("forces_incentives", "")),
        first_principles_mechanism=str(d.get("first_principles_mechanism", "")),
        first_principles=str(d.get("first_principles", "")),
        derivable_effects=[str(e) for e in derivable],
        speculative_effects=[str(e) for e in speculative],
        second_order_effects="",
        opportunities=[str(o) for o in opps[:3]],
        observation_metrics=[str(m) for m in obs],
        counter_risks=[str(r_) for r_ in risks],
        strategic_outlook_3y=str(d.get("strategic_outlook_3y", "")),
        signal_strength=signal,
        evidence_density=round(ev_density, 2),
    )


# ---------------------------------------------------------------------------
# Per-item analysis: Fallback path (evidence-driven, item-specific)
# ---------------------------------------------------------------------------


def _fallback_core_facts(r: MergedResult) -> list[str]:
    """Extract core facts from key_points (directly from text, not predictions)."""
    a = r.schema_a
    facts: list[str] = []

    # Use key_points as core facts (they come from the first sentences of the body)
    for kp in a.key_points[:3]:
        # Strip any leading "Hi HN" / "Show HN" noise
        cleaned = re.sub(r"^(Hi|Show|Ask|Tell)\s+(HN|Hacker News)[,:]?\s*", "", kp, flags=re.IGNORECASE)
        if len(cleaned.strip()) > 10:
            facts.append(cleaned.strip())

    # If no key_points, use title as a fact
    if not facts and a.title_zh:
        facts.append(a.title_zh)

    return facts


def _fallback_event_breakdown(r: MergedResult, core_facts: list[str], evidence: list[str]) -> str:
    """Build event breakdown from core facts + evidence."""
    parts = []
    if core_facts:
        parts.append("核心事實：")
        for i, fact in enumerate(core_facts, 1):
            parts.append(f"  {i}. {fact}")
    if evidence:
        parts.append("證據片段：")
        for e in evidence:
            parts.append(f'  > "{e}"')
    if r.schema_a.entities:
        parts.append(f"涉及實體：{'、'.join(r.schema_a.entities[:5])}")
    return "\n".join(parts) if parts else "暫無詳細事件拆解。"


def _fallback_forces_incentives(r: MergedResult) -> str:
    """Generate item-specific forces & incentives analysis."""
    a = r.schema_a
    cat = a.category or "綜合資訊"
    entities = a.entities[:3]

    stakeholders = _get_stakeholders(cat, entities)

    lines = []
    for name, motivation, constraint in stakeholders:
        lines.append(f"- {name}：{motivation}（約束：{constraint}）")

    return "\n".join(lines) if lines else "暫無利益相關方分析。"


def _fallback_first_principles(r: MergedResult) -> tuple[str, str]:
    """Select ONE mechanism and explain it using item-specific concepts.

    Returns (mechanism_name, analysis_text).
    """
    a = r.schema_a
    body = a.summary_zh or ""
    title = a.title_zh or ""

    mechanism = _select_mechanism(title, body)

    # Build item-specific explanation
    entity_str = "、".join(a.entities[:3]) if a.entities else "相關參與者"

    text = f"核心機制：{mechanism}\n該事件的底層邏輯與「{mechanism}」直接相關。對 {entity_str} 而言，"

    # Add mechanism-specific reasoning
    mech_lower = mechanism.lower()
    if "scalability" in mech_lower:
        text += "關鍵問題在於能否在用戶或資料量增長時維持成本效率與效能表現。"
    elif "latency" in mech_lower:
        text += "效能瓶頸將決定用戶體驗與實際可用性，毫秒級差異可能影響採用率。"
    elif "privacy" in mech_lower:
        text += "隱私合規要求與用戶信任構成核心約束，技術選擇必須平衡功能與保護。"
    elif "interoperability" in mech_lower:
        text += "與現有系統的整合能力決定採用門檻，標準化程度影響生態擴展速度。"
    elif "regulatory" in mech_lower:
        text += "監管態勢直接影響可行性與時程，政策變動可能重塑競爭格局。"
    elif "incentive" in mech_lower:
        text += "各方激勵結構的對齊程度決定合作可能性，錯位的激勵將阻礙推進。"
    elif "supply chain" in mech_lower:
        text += "供應鏈的穩定性與成本結構是規模化的基礎約束。"
    elif "security" in mech_lower:
        text += "安全邊界的完整性決定系統可信度，任何漏洞都可能造成連鎖影響。"
    else:  # adoption curve
        text += "目前處於採用曲線的哪個階段將決定策略重心——早期需聚焦驗證，後期需聚焦規模。"

    return mechanism, text


def _fallback_derivable_effects(r: MergedResult) -> list[str]:
    """Generate derivable (low-speculation) effects based on item content."""
    a, b = r.schema_a, r.schema_b
    effects: list[str] = []
    entities = a.entities[:2]
    entity_str = "、".join(entities) if entities else "相關方"

    # Always include one content-based effect
    if a.key_points:
        first_fact = a.key_points[0][:60]
        effects.append(f"基於「{first_fact}」，{entity_str} 的現有用戶／合作方需要評估相容性影響")

    if b.heat >= 7:
        effects.append(f"高關注度（熱度 {b.heat:.0f}）將促使同業加速跟進或發表回應")

    if b.novelty >= 7:
        effects.append(f"該方案的新穎性（{b.novelty:.0f}）可能吸引技術社群深入討論與複製嘗試")
    elif b.utility >= 7:
        effects.append(f"高實用性（{b.utility:.0f}）意味著下游開發者可能在短期內開始整合")

    return effects if effects else ["目前資訊不足以推導明確的直接影響"]


def _fallback_speculative_effects(r: MergedResult, mechanism: str = "") -> list[str]:
    """Generate clearly labeled speculative effects with validation signals."""
    a, b = r.schema_a, r.schema_b
    effects: list[str] = []
    entities = a.entities[:2]
    entity_str = "、".join(entities) if entities else "相關方"
    cat = a.category or "綜合資訊"
    mech_short = mechanism.split("（")[0] if "（" in mechanism else (mechanism or "相關領域")

    if b.novelty >= 6:
        effects.append(
            f"[假說] 若 {entity_str} 在 {mech_short} 方面的突破被市場驗證，"
            f"可能重塑 {cat} 領域的競爭格局"
            f"（驗證信號：關注 3 個月內 {entity_str} 相關產品的採用率與媒體報導量）"
        )

    if b.feasibility >= 6 and b.utility >= 6:
        effects.append(
            f"[假說] {entity_str} 的 {mech_short} 方案若證明可行，"
            f"可能引發 {cat} 領域更大規模的資源投入"
            f"（驗證信號：觀察下一季度 {cat} 領域的融資金額與人才流動）"
        )

    if not effects:
        effects.append(
            f"[假說] {entity_str} 的動態可能透過 {mech_short} 間接影響上下游產業鏈"
            f"（驗證信號：追蹤 {entity_str} 相關供應商或客戶的公開動態）"
        )

    return effects


def _fallback_opportunities(r: MergedResult, mechanism: str) -> list[str]:
    """Generate max 3 opportunities, each tied to mechanism + stakeholder."""
    a = r.schema_a
    entities = a.entities[:3]
    entity_str = "、".join(entities) if entities else "核心參與者"
    cat = a.category or "綜合資訊"

    # Get stakeholders
    stakeholder_data = _get_stakeholders(cat, entities)
    stakeholder_names = [s[0] for s in stakeholder_data]

    opps: list[str] = []

    # Opportunity 1: directly from mechanism
    mech_short = mechanism.split("（")[0] if "（" in mechanism else mechanism
    if stakeholder_names:
        opps.append(f"針對 {stakeholder_names[0]} 的 {mech_short} 需求，可探索為 {entity_str} 提供相關工具或服務的機會")

    # Opportunity 2: from entities
    if len(entities) >= 2:
        opps.append(f"{entities[0]} 與 {entities[1]} 之間的互動空間可能產生整合或橋接機會")
    elif entities:
        opps.append(f"圍繞 {entities[0]} 的上下游生態存在服務缺口，可評估補充性產品或服務的可行性")

    # Opportunity 3: from category-specific signal
    if a.key_points:
        fact = a.key_points[0][:40]
        if len(stakeholder_names) >= 2:
            opps.append(f"基於「{fact}」的趨勢，{stakeholder_names[1]} 可能需要新的解決方案來適應變化")

    return opps[:3]


def _fallback_observation_metrics(r: MergedResult) -> list[str]:
    """Generate 3-5 measurable observation metrics."""
    a = r.schema_a
    entities = a.entities[:2]
    metrics: list[str] = []

    if entities:
        metrics.append(f"{entities[0]} 的公開產品更新或版本發布頻率")
    metrics.append("相關領域的季度融資總額與交易數量")
    metrics.append("技術社群（GitHub stars、HN 討論數）的參與度趨勢")
    if len(entities) >= 2:
        metrics.append(f"{entities[1]} 的市場份額或用戶數變化")
    metrics.append("監管機構相關政策或指導文件的發布動態")

    return metrics[:5]


def _fallback_counter_risks(r: MergedResult) -> list[str]:
    """Generate 1-2 counter-examples/risks that could invalidate the outlook."""
    a = r.schema_a
    entities = a.entities[:2]
    entity_str = "、".join(entities) if entities else "相關參與者"

    risks: list[str] = [
        f"若 {entity_str} 未能持續投入資源，該方向可能失去動能並被替代方案取代",
    ]

    if a.category in ("政策/監管", "資安/網路安全"):
        risks.append("突發的監管政策變動可能徹底改變可行性與時程預期")
    else:
        risks.append("技術或市場環境的快速變化可能使當前評估在 6-12 個月後過時")

    return risks[:2]


def _fallback_strategic_outlook(r: MergedResult, mechanism: str, metrics: list[str], risks: list[str]) -> str:
    """Generate item-specific 3-year strategic outlook."""
    b = r.schema_b
    a = r.schema_a
    entities = a.entities[:2]
    entity_str = "、".join(entities) if entities else "相關方"
    mech_short = mechanism.split("（")[0] if "（" in mechanism else mechanism

    if b.feasibility >= 7:
        timeline = "短期（0-12 個月）即可見到實質性進展"
    elif b.feasibility >= 5:
        timeline = "中期（1-2 年）將逐步顯現影響"
    else:
        timeline = "長期（2-3 年）才可能看到規模化落地"

    parts = [
        f"基於 {mech_short} 的分析框架，{entity_str} 的動態預計在{timeline}。",
    ]

    if metrics:
        parts.append(f"觀察指標：{'、'.join(metrics[:3])}。")

    if risks:
        parts.append(f"主要風險：{risks[0]}")

    return "\n".join(parts)


def _analyze_item_fallback(r: MergedResult) -> ItemDeepDive:
    """Analyze a single item using evidence-driven heuristics (not boilerplate)."""
    a = r.schema_a

    # 1. Core facts & evidence
    core_facts = _fallback_core_facts(r)
    body_text = a.summary_zh or ""
    evidence = _extract_evidence_excerpts(body_text, max_excerpts=2)

    # 2. Evidence density
    ev_density = len(evidence) / max(len(core_facts), 1)

    # 3. Event breakdown
    event_breakdown = _fallback_event_breakdown(r, core_facts, evidence)

    # 4. Forces & incentives
    forces = _fallback_forces_incentives(r)

    # 5. First principles (select ONE mechanism)
    mechanism, fp_text = _fallback_first_principles(r)

    # 6. Second-order effects (split)
    derivable = _fallback_derivable_effects(r)
    speculative = _fallback_speculative_effects(r, mechanism)

    # 7. Opportunities (tied to mechanism + stakeholder)
    opportunities = _fallback_opportunities(r, mechanism)

    # 8. Observation metrics & counter risks
    metrics = _fallback_observation_metrics(r)
    risks = _fallback_counter_risks(r)

    # 9. Strategic outlook
    outlook = _fallback_strategic_outlook(r, mechanism, metrics, risks)

    # 10. Signal strength
    signal = _compute_signal_strength(r.schema_b, ev_density)

    return ItemDeepDive(
        item_id=r.item_id,
        core_facts=core_facts,
        evidence_excerpts=evidence,
        event_breakdown=event_breakdown,
        forces_incentives=forces,
        first_principles_mechanism=mechanism,
        first_principles=fp_text,
        derivable_effects=derivable,
        speculative_effects=speculative,
        second_order_effects="",  # backward compat: now split into derivable + speculative
        opportunities=opportunities,
        observation_metrics=metrics,
        counter_risks=risks,
        strategic_outlook_3y=outlook,
        signal_strength=signal,
        evidence_density=round(ev_density, 2),
    )


# ---------------------------------------------------------------------------
# Cross-news meta analysis: LLM path
# ---------------------------------------------------------------------------

_META_ANALYSIS_PROMPT = """你是一位頂級戰略情報分析師。請基於以下多條資訊進行跨新聞元分析。

資訊列表:
{items_summary}

請以嚴格的 JSON 格式回傳:
{{
  "executive_meta_signals": "執行層元信號：當前情報批次中最重要的 3-5 個宏觀信號及其含義（300-500字）",
  "emerging_macro_themes": "湧現宏觀主題：從這批情報中識別出的 2-4 個跨領域宏觀主題（300-500字）",
  "opportunity_map": "機會地圖：綜合所有情報生成的戰略機會矩陣，包含短期／中期／長期維度（300-500字）",
  "actionable_signals": "可執行信號：最值得立即行動的 3-5 個具體信號及建議行動（300-500字）"
}}"""


def _meta_analysis_llm(results: list[MergedResult]) -> dict:
    """Cross-news meta analysis via LLM."""
    summaries = []
    for i, r in enumerate(results, 1):
        a, b = r.schema_a, r.schema_b
        summaries.append(
            f"{i}. [{a.category}] {a.title_zh or '（無標題）'} "
            f"(score={b.final_score}, novelty={b.novelty}, heat={b.heat}) "
            f"摘要: {(a.summary_zh or '')[:100]}"
        )

    prompt = _META_ANALYSIS_PROMPT.format(items_summary="\n".join(summaries))
    raw = _chat_completion([{"role": "user", "content": prompt}], temperature=0.4)
    return _parse_json_from_llm(raw)


# ---------------------------------------------------------------------------
# Cross-news meta analysis: Fallback path (improved entity filtering)
# ---------------------------------------------------------------------------


def _meta_analysis_fallback(results: list[MergedResult]) -> dict:
    """Cross-news meta analysis using filtered entities, category distribution, score aggregation."""
    cat_counter: Counter[str] = Counter()
    tag_counter: Counter[str] = Counter()
    entity_counter: Counter[str] = Counter()
    total_score = 0.0
    total_novelty = 0.0
    total_heat = 0.0
    n = len(results)

    for r in results:
        cat_counter[r.schema_a.category or "綜合資訊"] += 1
        for t in r.schema_b.tags:
            tag_counter[t] += 1
        # Entities are now pre-filtered by entity_extraction module
        for e in r.schema_a.entities:
            entity_counter[e] += 1
        total_score += r.schema_b.final_score
        total_novelty += r.schema_b.novelty
        total_heat += r.schema_b.heat

    avg_score = round(total_score / n, 1) if n else 0
    avg_novelty = round(total_novelty / n, 1) if n else 0
    avg_heat = round(total_heat / n, 1) if n else 0

    top_cats = cat_counter.most_common(5)
    top_tags = tag_counter.most_common(5)
    top_entities = entity_counter.most_common(8)

    # --- Executive Meta Signals ---
    cat_dist = "、".join(f"{c}（{cnt} 條）" for c, cnt in top_cats)
    exec_signals = (
        f"本批次共處理 {n} 條高品質情報，平均分數 {avg_score}，"
        f"平均新穎度 {avg_novelty}，平均熱度 {avg_heat}。"
        f"分類分佈：{cat_dist}。"
    )
    if top_entities:
        exec_signals += f"高頻實體：{', '.join(e for e, _ in top_entities[:5])}，表明市場關注焦點集中於此。"
    if avg_novelty >= 7:
        exec_signals += "整體新穎度偏高，預示行業可能進入變革期。"
    elif avg_heat >= 7:
        exec_signals += "整體熱度偏高，建議關注短期競爭態勢變化。"

    # Check for political sensitivity
    political_entities = {"Trump", "Biden", "Congress", "Senate", "White House"}
    found_political = [e for e, _ in top_entities if e in political_entities]
    if found_political:
        exec_signals += f"注意：本批次涉及政治敏感實體（{'、'.join(found_political)}），相關分析保持中立事實陳述。"

    # --- Emerging Macro Themes ---
    themes = []
    for cat, cnt in top_cats:
        if cnt >= 2:
            themes.append(f"{cat} 領域出現聚集信號（{cnt} 條），可能形成階段性主題")
    if top_tags:
        themes.append(f"高頻標籤 {', '.join(t for t, _ in top_tags[:3])} 反映了當前市場的核心關注方向")
    if not themes:
        themes.append("本批次情報較為分散，尚未形成明顯聚集性主題")
    macro_themes = "；".join(themes) + "。"

    # --- Opportunity Map ---
    high_score_items = [r for r in results if r.schema_b.final_score >= 8]
    high_novelty_items = [r for r in results if r.schema_b.novelty >= 7]

    opp_parts = []
    if high_score_items:
        titles = "、".join((r.schema_a.title_zh or r.item_id)[:20] for r in high_score_items[:3])
        opp_parts.append(f"短期重點關注（高綜合分）：{titles}")
    if high_novelty_items:
        titles = "、".join((r.schema_a.title_zh or r.item_id)[:20] for r in high_novelty_items[:3])
        opp_parts.append(f"中期戰略佈局（高新穎度）：{titles}")
    opp_parts.append("長期趨勢：持續監測" + ("、".join(c for c, _ in top_cats) if top_cats else "各領域") + "的演進")
    opp_map = "。".join(opp_parts) + "。"

    # --- Actionable Signals ---
    sorted_by_signal = sorted(results, key=lambda r: _compute_signal_strength(r.schema_b), reverse=True)
    actions = []
    for r in sorted_by_signal[:3]:
        sig = _compute_signal_strength(r.schema_b)
        title = (r.schema_a.title_zh or r.item_id)[:30]
        actions.append(f"[信號強度 {sig:.1f}] {title} — 建議深入調研並評估行動方案")
    actionable = "；".join(actions) + "。" if actions else "本批次無突出可執行信號。"

    return {
        "executive_meta_signals": exec_signals,
        "emerging_macro_themes": macro_themes,
        "opportunity_map": opp_map,
        "actionable_signals": actionable,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def analyze_batch(results: list[MergedResult]) -> DeepAnalysisReport:
    """Analyze a batch of passed results and produce a DeepAnalysisReport.

    This is the main entry point for Z4. Errors are logged but never raised
    to avoid killing the pipeline.
    """
    log = get_logger()
    log.info("--- Z4: 深度分析 ---")
    log.info("正在深度分析 %d 筆項目", len(results))

    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    use_llm = _llm_available()

    # Per-item analysis
    per_item: list[ItemDeepDive] = []
    for r in results:
        try:
            if use_llm:
                try:
                    dive = _analyze_item_llm(r)
                except Exception as exc:
                    log.warning("深度分析 LLM 失敗（%s），改用規則引擎：%s", r.item_id, exc)
                    dive = _analyze_item_fallback(r)
            else:
                dive = _analyze_item_fallback(r)
            per_item.append(dive)
        except Exception as exc:
            log.error("深度分析失敗（%s）：%s", r.item_id, exc)
            per_item.append(ItemDeepDive(item_id=r.item_id))

    # Cross-news meta analysis
    meta: dict = {}
    try:
        if use_llm:
            try:
                meta = _meta_analysis_llm(results)
            except Exception as exc:
                log.warning("跨新聞元分析 LLM 失敗，改用規則引擎：%s", exc)
                meta = _meta_analysis_fallback(results)
        else:
            meta = _meta_analysis_fallback(results)
    except Exception as exc:
        log.error("跨新聞元分析完全失敗：%s", exc)

    report = DeepAnalysisReport(
        generated_at=now,
        total_items=len(results),
        executive_meta_signals=str(meta.get("executive_meta_signals", "")),
        per_item_analysis=per_item,
        emerging_macro_themes=str(meta.get("emerging_macro_themes", "")),
        opportunity_map=str(meta.get("opportunity_map", "")),
        actionable_signals=str(meta.get("actionable_signals", "")),
    )

    log.info("深度分析完成：共 %d 筆項目已分析", len(per_item))
    return report
