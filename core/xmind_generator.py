"""XMind 總經理版心智圖生成器。

輸出 outputs/mindmap.xmind（XMind 2020 zip 格式）。
結構：content.json + metadata.json，可被 XMind 正常開啟。

禁用詞彙：ai捕捉、AI Intel、Z1~Z5、pipeline、ETL、verify_run、ingestion、ai_core
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from schemas.education_models import EduNewsCard, SystemHealthReport, translate_fail_reason
from utils.logger import get_logger


def _build_topic(title: str, children: list[dict] | None = None) -> dict:
    """Build an XMind topic node."""
    topic: dict = {"title": title}
    if children:
        topic["children"] = {"attached": children}
    return topic


def _build_content_json(
    cards: list[EduNewsCard],
    health: SystemHealthReport,
    report_time: str,
) -> list[dict]:
    """Build the XMind content.json structure."""
    date_str = report_time.split(" ")[0] if " " in report_time else report_time

    # Root children
    root_children: list[dict] = []

    # News items
    valid_cards = [c for c in cards if c.is_valid_news]
    for i, card in enumerate(valid_cards, 1):
        card_children = [
            _build_topic(f"是什麼：{card.what_happened[:60]}"),
            _build_topic(f"為何重要：{card.why_important[:60]}"),
        ]

        # Risks
        risks = card.speculative_effects[:2] if card.speculative_effects else ["低"]
        risk_children = [_build_topic(r[:50]) for r in risks]
        card_children.append(_build_topic("風險", risk_children))

        # Suggestions
        actions = card.action_items[:2] if card.action_items else ["持續觀察"]
        act_children = [_build_topic(a[:50]) for a in actions]
        card_children.append(_build_topic("建議", act_children))

        # Who to ask
        card_children.append(_build_topic("要問誰：相關部門主管或產業分析師"))

        root_children.append(
            _build_topic(f"新聞{i}：{card.title_plain[:30]}", card_children)
        )

    # Invalid items (brief)
    invalid_cards = [c for c in cards if not c.is_valid_news]
    if invalid_cards:
        inv_children = [
            _build_topic(f"無效項目 {i}：{c.invalid_cause or '資料異常'}")
            for i, c in enumerate(invalid_cards, 1)
        ]
        root_children.append(_build_topic(f"無效內容（{len(invalid_cards)} 則）", inv_children))

    # System health (in plain language)
    health_children = [
        _build_topic(f"資料完整率：{health.success_rate:.0f}%"),
        _build_topic(f"處理延遲：中位數 {health.p50_latency:.1f}s"),
        _build_topic(f"處理時間：{health.total_runtime:.1f}s"),
    ]
    if health.fail_reasons:
        fail_children = [
            _build_topic(f"{translate_fail_reason(k)}：{v} 次")
            for k, v in health.fail_reasons.items()
        ]
        health_children.append(_build_topic("主要異常類型", fail_children))
    root_children.append(_build_topic("系統運作概況", health_children))

    # Build sheet
    root_topic = _build_topic(
        f"每日科技趨勢（{date_str}）", root_children,
    )

    sheet = {
        "id": "sheet-1",
        "class": "sheet",
        "title": f"趨勢簡報 {date_str}",
        "rootTopic": root_topic,
    }

    return [sheet]


def _build_metadata_json() -> dict:
    return {
        "creator": {
            "name": "Daily Tech Intelligence Briefing",
            "version": "1.0.0",
        },
    }


def generate_xmind(
    cards: list[EduNewsCard],
    health: SystemHealthReport,
    report_time: str,
    output_path: Path | None = None,
) -> Path:
    """Generate an XMind 2020 mindmap file (.xmind = zip).

    Returns the path to the generated .xmind file.
    """
    log = get_logger()
    if output_path is None:
        project_root = Path(__file__).resolve().parent.parent
        output_path = project_root / "outputs" / "mindmap.xmind"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    content = _build_content_json(cards, health, report_time)
    metadata = _build_metadata_json()

    with zipfile.ZipFile(str(output_path), "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("content.json", json.dumps(content, ensure_ascii=False, indent=2))
        zf.writestr("metadata.json", json.dumps(metadata, ensure_ascii=False, indent=2))

    log.info("XMind mindmap generated: %s", output_path)
    return output_path
