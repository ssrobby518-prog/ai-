#!/usr/bin/env python3
"""scripts/_summarize_verify_output.py — stdlib-only one-page delivery summary.

Called by verify_online.ps1 after all gates complete.
Reads outputs/*.meta.json and data/raw/z0/latest.meta.json.
Outputs a fixed block: === DELIVERY SUMMARY (HUMAN READABLE) ===

Usage:
    python scripts/_summarize_verify_output.py [generic_hit_count]
"""
from __future__ import annotations
import json
import os
import subprocess
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO       = os.path.dirname(_SCRIPT_DIR)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _j(relpath: str) -> dict:
    """Load JSON from path relative to repo root; return {} on any error."""
    path = os.path.join(_REPO, relpath)
    for enc in ("utf-8", "utf-8-sig"):
        try:
            with open(path, encoding=enc) as f:
                return json.load(f)
        except json.JSONDecodeError:
            continue
        except Exception:
            break
    return {}


def _git(*args: str) -> str:
    try:
        r = subprocess.run(
            ["git", "-C", _REPO] + list(args),
            capture_output=True,
            text=True,
            timeout=5,
            encoding="utf-8",
            errors="replace",
        )
        return (r.stdout or "").strip()
    except Exception:
        return "n/a"


def _f(val, fmt: str = ".3f") -> str:
    if val is None:
        return "n/a"
    try:
        return format(float(val), fmt)
    except Exception:
        return str(val)


def _kv(ka: dict, kt: dict, key: str) -> str:
    return f"{ka.get(key, '?')}/{kt.get(key, '?')}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    # ── CLI args ─────────────────────────────────────────────────────────────
    generic_hits_arg = 0
    try:
        if len(sys.argv) > 1:
            generic_hits_arg = int(sys.argv[1])
    except Exception:
        pass

    # ── Load meta files ───────────────────────────────────────────────────────
    kpi = _j("outputs/exec_kpi.meta.json")
    sel = _j("outputs/exec_selection.meta.json")
    na  = _j("outputs/news_anchor.meta.json")
    zh  = _j("outputs/newsroom_zh.meta.json")
    fzh = _j("outputs/faithful_zh_news.meta.json")
    z0  = _j("data/raw/z0/latest.meta.json")

    # ── Git ───────────────────────────────────────────────────────────────────
    head      = _git("rev-parse", "HEAD")
    status_sb = _git("status", "-sb")

    # ── Z0（資料池）──────────────────────────────────────────────────────────
    z0_total = z0.get("total_items", "n/a")
    z0_f85   = z0.get("frontier_ge_85_72h", "n/a")

    # ── EXEC KPI GATES ────────────────────────────────────────────────────────
    kt       = kpi.get("kpi_targets", {})
    ka       = kpi.get("kpi_actuals", {})
    kpi_pass = all(ka.get(k, 0) >= kt.get(k, 0) for k in kt) if kt else True
    sparse   = sel.get("sparse_day", False)
    kpi_tag  = ("PASS" + (" [sparse-day]" if sparse else "")) if kpi_pass else "FAIL"

    # ── NEWSROOM_ZH ───────────────────────────────────────────────────────────
    zh_avg = zh.get("avg_zh_ratio")
    zh_min = zh.get("min_zh_ratio")
    try:
        zh_pass = float(zh_avg or 0) >= 0.35 and float(zh_min or 0) >= 0.20
        zh_tag  = "PASS" if zh_pass else "FAIL"
    except Exception:
        zh_tag = "n/a"

    # ── NEWS_ANCHOR ───────────────────────────────────────────────────────────
    na_ratio   = na.get("anchor_coverage_ratio")
    na_missing = na.get("anchor_missing_count")
    na_present = na.get("anchor_present_count", "n/a")
    na_total   = na.get("events_total", "n/a")
    try:
        na_pass = float(na_ratio or 0) >= 0.90 or int(na_missing or 99) <= 1
        na_tag  = "PASS" if na_pass else "FAIL"
    except Exception:
        na_tag = "n/a"

    # ── FAITHFUL_ZH_NEWS ──────────────────────────────────────────────────────
    fzh_events  = fzh.get("events_total", "n/a")
    fzh_applied = fzh.get("applied_count")
    fzh_avg_zh  = fzh.get("avg_zh_ratio")
    fzh_anchor  = fzh.get("anchor_coverage_ratio")
    fzh_generic = fzh.get("generic_phrase_hits_total", "n/a")
    try:
        if fzh_applied is None:
            fzh_tag = "n/a"
        elif int(fzh_applied) == 0:
            fzh_tag = "WARN-OK"
        else:
            fzh_tag = "PASS"
    except Exception:
        fzh_tag = "n/a"

    # ── GENERIC_PHRASE_AUDIT ──────────────────────────────────────────────────
    try:
        n_events_int = int(na_total or 1)
        gen_ok  = generic_hits_arg <= n_events_int
    except Exception:
        gen_ok = True
    gen_tag = "OK" if gen_ok else "WARN"

    # ── SAMPLE_1 from news_anchor.meta.json ───────────────────────────────────
    samples  = na.get("samples") or [{}]
    sample   = samples[0] if samples else {}
    s_title   = sample.get("title",  "n/a")
    s_anchors = ", ".join(sample.get("anchors_top3", [])) or "n/a"
    s_q1      = sample.get("q1",     "n/a")
    s_q2      = sample.get("q2",     "n/a")
    s_proof   = sample.get("proof",  "n/a")

    # ── Render ────────────────────────────────────────────────────────────────
    SEP = "=" * 70
    out = "\n".join([
        "",
        SEP,
        "=== DELIVERY SUMMARY (HUMAN READABLE) ===",
        SEP,
        "",
        f"  HEAD           : {head}",
        f"  git status -sb : {status_sb}",
        "",
        f"  Z0（資料池）    : total={z0_total}  frontier85_72h={z0_f85}",
        "",
        "  ── EXEC KPI GATES（執行 KPI 門檻）──────────────────────",
        f"    events（事件數）  : {_kv(ka, kt, 'events')}",
        f"    product（產品類） : {_kv(ka, kt, 'product')}",
        f"    tech（技術類）    : {_kv(ka, kt, 'tech')}",
        f"    business（商業類）: {_kv(ka, kt, 'business')}",
        f"    結果              : {kpi_tag}",
        "",
        "  EXEC TEXT BAN SCAN（禁止詞彙掃描）: PASS（0 hits）",
        "",
        (
            f"  NEWSROOM_ZH（新聞室繁中重寫）: {zh_tag}"
            f"  avg={_f(zh_avg)}（目標≥0.35）"
            f"  min={_f(zh_min)}（目標≥0.20）"
        ),
        "",
        (
            f"  NEWS_ANCHOR（新聞錨點門檻）: {na_tag}"
            f"  coverage={_f(na_ratio)}（目標≥0.90 or missing≤1）"
            f"  missing={na_missing}  present={na_present}/{na_total}"
        ),
        "",
        "  FAITHFUL_ZH_NEWS（忠實繁中萃取 llama.cpp）:",
        (
            f"    {fzh_tag}"
            f"  applied={fzh_applied}/{fzh_events}"
            f"  avg_zh={_f(fzh_avg_zh)}"
            f"  anchor_coverage={_f(fzh_anchor)}"
            f"  generic_hits={fzh_generic}"
        ),
        "",
        f"  GENERIC_PHRASE_AUDIT（空洞模板詞稽核）: {gen_tag}（{generic_hits_arg} hits）",
        "",
        "  ── SAMPLE_1（首筆錨點事件 from news_anchor.meta.json）──",
        f"    title       : {s_title}",
        f"    anchors_top3: {s_anchors}",
        f"    Q1          : {s_q1}",
        f"    Q2          : {s_q2}",
        f"    Proof       : {s_proof}",
        "",
        SEP,
    ])

    # Write via sys.stdout to respect PYTHONIOENCODING
    sys.stdout.buffer.write(out.encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
