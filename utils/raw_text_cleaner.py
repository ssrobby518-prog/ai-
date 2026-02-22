"""utils/raw_text_cleaner.py — UI 垃圾清洗器（stdlib only）。

提供：
    clean_raw_text(text: str) -> str
    ui_garbage_score(text: str) -> float
    contains_disallowed_ui_tokens(text: str) -> bool
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# UI 垃圾模式（大小寫不敏感）；每個 pattern 帶加權分數
# ---------------------------------------------------------------------------

# 強規則：命中整行即刪（不限行長）
_UI_STRONG: list[tuple[re.Pattern, float]] = [
    (re.compile(r"you must be signed in", re.IGNORECASE), 2.0),
    (re.compile(r"change notification settings", re.IGNORECASE), 2.0),
    (re.compile(r"skip to content", re.IGNORECASE), 2.0),
    (re.compile(r"enable javascript", re.IGNORECASE), 2.0),
    (re.compile(r"\bsign in\b", re.IGNORECASE), 1.5),
    (re.compile(r"\blog in\b", re.IGNORECASE), 1.5),
    (re.compile(r"\bnotifications?\b", re.IGNORECASE), 1.0),
    (re.compile(r"\bterms\b", re.IGNORECASE), 1.0),
    (re.compile(r"\bprivacy\b", re.IGNORECASE), 1.0),
    (re.compile(r"\bcookies?\b", re.IGNORECASE), 1.0),
]

# GitHub UI 行：僅在行長 <= 40 時才刪（避免正文誤刪）
_UI_GITHUB_SHORT: list[tuple[re.Pattern, float]] = [
    (re.compile(r"\bwatch\b", re.IGNORECASE), 0.8),
    (re.compile(r"\bfork\b", re.IGNORECASE), 0.8),
    (re.compile(r"\bstar\b", re.IGNORECASE), 0.8),
]

_MAX_OUTPUT_CHARS: int = 12_000


# ---------------------------------------------------------------------------
# 輔助
# ---------------------------------------------------------------------------

def _normalize_line_endings(text: str) -> str:
    """CRLF → LF；壓縮連續空行（最多保留 1 行）。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.rstrip() for ln in text.split("\n")]
    out: list[str] = []
    blank_run = 0
    for ln in lines:
        if not ln.strip():
            blank_run += 1
            if blank_run <= 1:
                out.append("")
        else:
            blank_run = 0
            out.append(ln)
    return "\n".join(out).strip()


def _is_ui_line(line: str) -> bool:
    """判定單行是否為 UI/導航/按鈕垃圾行。"""
    stripped = line.strip()
    if not stripped:
        return False
    # 強規則（不限行長）
    for pat, _ in _UI_STRONG:
        if pat.search(stripped):
            return True
    # GitHub UI 短行（行長 <= 40 才刪）
    if len(stripped) <= 40:
        for pat, _ in _UI_GITHUB_SHORT:
            if pat.search(stripped):
                return True
    return False


def _symbol_density(line: str) -> float:
    """符號密度：非字母數字中文字元佔比。"""
    if not line:
        return 0.0
    alnum_cjk = sum(
        1 for c in line
        if c.isalnum() or "\u4e00" <= c <= "\u9fff"
    )
    return 1.0 - alnum_cjk / len(line)


# ---------------------------------------------------------------------------
# 公開 API
# ---------------------------------------------------------------------------

def clean_raw_text(text: str) -> str:
    """清洗原始文字：移除 UI 垃圾、符號密度過高的短行，輸出最多 12000 字元。

    步驟：
    1. 正規化（CRLF→LF、去多餘空白行）
    2. 逐行過濾 UI/導航/按鈕垃圾行
    3. 過濾符號密度 > 50% 且長度 < 60 的行
    4. 截斷至 12000 字元
    """
    if not text:
        return ""
    text = _normalize_line_endings(text)
    lines = text.split("\n")
    kept: list[str] = []
    for line in lines:
        stripped = line.strip()
        if _is_ui_line(stripped):
            continue
        if stripped and len(stripped) < 60 and _symbol_density(stripped) > 0.5:
            continue
        kept.append(line)
    result = "\n".join(kept).strip()
    if len(result) > _MAX_OUTPUT_CHARS:
        cut = result[:_MAX_OUTPUT_CHARS]
        last_nl = cut.rfind("\n")
        if last_nl > int(_MAX_OUTPUT_CHARS * 0.8):
            cut = cut[:last_nl]
        result = cut
    return result


def ui_garbage_score(text: str) -> float:
    """回傳 0~1 的 UI 垃圾命中強度（命中行數比 × 0.6 + 加權分數比 × 0.4）。"""
    if not text:
        return 0.0
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    if not lines:
        return 0.0
    all_patterns = _UI_STRONG + _UI_GITHUB_SHORT
    max_weight = sum(w for _, w in all_patterns)
    hit_lines = 0
    weighted_hits = 0.0
    for line in lines:
        line_hit = False
        for pat, weight in all_patterns:
            if pat.search(line):
                if not line_hit:
                    hit_lines += 1
                    line_hit = True
                weighted_hits += weight
    line_ratio = hit_lines / len(lines)
    weight_ratio = min(1.0, weighted_hits / max(1.0, max_weight))
    return round(min(1.0, line_ratio * 0.6 + weight_ratio * 0.4), 4)


def contains_disallowed_ui_tokens(text: str) -> bool:
    """偵測文字是否仍含 UI 關鍵片語（最後一道保險）。"""
    if not text:
        return False
    for pat, _ in _UI_STRONG:
        if pat.search(text):
            return True
    return False
