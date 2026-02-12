"""Z4 – Deep Analysis delivery sink.

Writes the 5-part deep analysis report to Markdown.
Updated to render the evidence-driven structure (v2):
- Core facts + evidence excerpts
- First principles mechanism selection
- Split second-order effects (derivable vs speculative)
- Observation metrics + counter-risks
"""

from __future__ import annotations

from pathlib import Path

from config import settings
from schemas.models import DeepAnalysisReport
from utils.logger import get_logger


def write_deep_analysis(
    report: DeepAnalysisReport,
    output_path: Path | None = None,
) -> Path:
    """Generate deep_analysis.md with the 5-part structure."""
    log = get_logger()
    path = output_path or settings.DEEP_ANALYSIS_OUTPUT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = [
        "# AI 深度情報分析報告",
        "",
        f"生成時間: {report.generated_at}",
        f"分析項目數: {report.total_items}",
        "",
        "---",
        "",
    ]

    # PART 1: Executive Meta Signals
    lines.extend(
        [
            "## PART 1: 執行層元信號 (Executive Meta Signals)",
            "",
            report.executive_meta_signals or "*暫無元信號分析。*",
            "",
            "---",
            "",
        ]
    )

    # PART 2: Per-News Deep Dive
    lines.extend(
        [
            "## PART 2: 逐條深度分析 (Per-News Deep Dive)",
            "",
        ]
    )

    if not report.per_item_analysis:
        lines.append("*本次無項目進行深度分析。*\n")
    else:
        for i, dive in enumerate(report.per_item_analysis, 1):
            lines.extend(
                [
                    f"### {i}. {dive.item_id}",
                    "",
                    f"**信號強度: {dive.signal_strength:.1f}** | **證據密度: {dive.evidence_density:.0%}**",
                    "",
                ]
            )

            # A) Event Breakdown with core facts + evidence
            lines.append("#### 事件拆解 (Event Breakdown)")
            lines.append("")

            if dive.core_facts:
                lines.append("**核心事實：**")
                for fact in dive.core_facts:
                    lines.append(f"- {fact}")
                lines.append("")

            if dive.evidence_excerpts:
                lines.append("**證據片段：**")
                for excerpt in dive.evidence_excerpts:
                    lines.append(f'> "{excerpt}"')
                lines.append("")

            # Fallback: if event_breakdown is populated but core_facts is empty
            if not dive.core_facts and dive.event_breakdown:
                lines.append(dive.event_breakdown)
                lines.append("")

            # B) Forces & Incentives
            lines.extend(
                [
                    "#### 力場與激勵分析 (Forces & Incentives)",
                    "",
                    dive.forces_incentives or "*暫無*",
                    "",
                ]
            )

            # C) First Principles
            lines.append("#### 第一性原理 (First Principles)")
            lines.append("")
            if dive.first_principles_mechanism:
                lines.append(f"**選定機制：** {dive.first_principles_mechanism}")
                lines.append("")
            lines.append(dive.first_principles or "*暫無*")
            lines.append("")

            # D) Second-Order Effects (split)
            lines.append("#### 二階效應 (Second-Order Effects)")
            lines.append("")

            if dive.derivable_effects:
                lines.append("**可直接推導的影響：**")
                for eff in dive.derivable_effects:
                    lines.append(f"- {eff}")
                lines.append("")

            if dive.speculative_effects:
                lines.append("**需驗證的推測：**")
                for eff in dive.speculative_effects:
                    lines.append(f"- {eff}")
                lines.append("")

            # Fallback: old-style single string
            if not dive.derivable_effects and not dive.speculative_effects and dive.second_order_effects:
                lines.append(dive.second_order_effects)
                lines.append("")

            # E) Opportunities
            lines.extend(
                [
                    "#### 機會識別 (Opportunities)",
                    "",
                ]
            )
            if dive.opportunities:
                for opp in dive.opportunities:
                    lines.append(f"- {opp}")
            else:
                lines.append("- *暫無*")
            lines.append("")

            # F) Strategic Outlook
            lines.extend(
                [
                    "#### 3年戰略展望 (Strategic Outlook)",
                    "",
                    dive.strategic_outlook_3y or "*暫無*",
                    "",
                ]
            )

            # Observation metrics
            if dive.observation_metrics:
                lines.append("**觀察指標：**")
                for m in dive.observation_metrics:
                    lines.append(f"- {m}")
                lines.append("")

            # Counter-risks
            if dive.counter_risks:
                lines.append("**反例／風險：**")
                for r_ in dive.counter_risks:
                    lines.append(f"- {r_}")
                lines.append("")

            lines.extend(["---", ""])

    # PART 3: Emerging Macro Themes
    lines.extend(
        [
            "## PART 3: 湧現宏觀主題 (Emerging Macro Themes)",
            "",
            report.emerging_macro_themes or "*暫無宏觀主題分析。*",
            "",
            "---",
            "",
        ]
    )

    # PART 4: Opportunity Map
    lines.extend(
        [
            "## PART 4: 機會地圖 (Opportunity Map)",
            "",
            "| 維度 | 內容 |",
            "|------|------|",
        ]
    )

    if report.opportunity_map:
        segments = [s.strip() for s in report.opportunity_map.replace("。", "。\n").split("\n") if s.strip()]
        for seg in segments:
            if ":" in seg:
                label, content = seg.split(":", 1)
                lines.append(f"| {label.strip()} | {content.strip()} |")
            elif "：" in seg:
                label, content = seg.split("：", 1)
                lines.append(f"| {label.strip()} | {content.strip()} |")
            else:
                lines.append(f"| 綜合 | {seg} |")
    else:
        lines.append("| - | *暫無機會分析* |")

    lines.extend(
        [
            "",
            "---",
            "",
        ]
    )

    # PART 5: Actionable Signals
    lines.extend(
        [
            "## PART 5: 可執行信號 (Actionable Signals)",
            "",
            report.actionable_signals or "*暫無可執行信號。*",
            "",
            "---",
            "",
            "*本報告由 AI Intel Deep Analyzer (Z4) 自動生成*",
            "",
        ]
    )

    content = "\n".join(lines)
    path.write_text(content, encoding="utf-8")
    log.info("Deep analysis written to %s (%d items)", path, report.total_items)
    return path
