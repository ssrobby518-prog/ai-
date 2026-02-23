from __future__ import annotations

import atexit
import asyncio
import json
import logging
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional, Tuple, List

logger = logging.getLogger(__name__)

LLAMA_SERVER_EXE = (
    r"C:\Projects\ai捕捉資訊\qwen_inference_node_4060"
    r"\llama-b8123-bin-win-cuda-12.4-x64\llama-server.exe"
)
MODEL_GGUF = (
    r"C:\Projects\ai捕捉資訊\qwen_inference_node_4060"
    r"\Qwen2.5-7B-Instruct-Q4_K_M\Qwen2.5-7B-Instruct-Q4_K_M.gguf"
)

API_URL = "http://127.0.0.1:8080/v1/chat/completions"
HEALTH_URL = "http://127.0.0.1:8080/health"

MAX_INPUT_CHARS = 12_000
HTTP_TIMEOUT_S = 120
SERVER_READY_TIMEOUT_S = 120
SERVER_HEALTH_POLL_S = 2

_RE_ELLIPSIS = re.compile(r"\.\.\.|…|\u2026")
_RE_ANY_BRACES = re.compile(r"[{}]")
_RE_QUOTES = re.compile(r"「([^」]{1,240})」")

# tolerant block parser: accepts \r\n, extra blank lines in Q3, trailing newline at end
_RE_PARSE = re.compile(
    r"^Q1:\s*(?P<q1>.*?)\r?\n"
    r"Q2:\s*(?P<q2>.*?)\r?\n"
    r"Q3:\s*\r?\n(?P<q3>.*?)(?:\r?\n)+"
    r"Proof:\s*(?P<proof>.*?)\s*$",
    re.S,
)

_GENERIC_PHRASES = [
    "引發業界廣泛關注",
    "具有重要意義",
    "密切追蹤",
    "新的參考基準",
    "各方正密切追蹤後續進展",
    "各大廠商與投資人正密切評估",
]

_BAD_CLAUDE = ["克勞德", "克劳德"]

SYSTEM_PROMPT = (
    "你是 BBC 科技新聞審校引擎（繁體中文）。\n"
    "任務：對輸入原文做「抽取式摘要 + 忠實翻譯」，禁止捏造。\n"
    "硬規則（任何一條違反都必須重做）：\n"
    "1) 禁止省略號：不得出現「...」或「…」。\n"
    "2) 禁止括號 placeholder：不得輸出任何 { } 字元。\n"
    "3) 提及 Claude 必須寫成：Claude（Anthropic）；禁止音譯。\n"
    "4) Q1 與 Q2 各至少 1 個原文逐字 quote token，用「」包住；token 必須是原文的逐字子字串。\n"
    "5) 禁止空話：不得使用「引發關注/重要意義/密切追蹤/參考基準」等泛句，必須改抽取原文具體陳述。\n"
    "輸出格式（只允許以下四段，不得多字）：\n"
    "Q1: 兩句，每句不超過 80 字；至少 1 句含原文逐字 token（用「」包住）\n"
    "Q2: 兩句，每句不超過 80 字；至少 1 句含原文逐字 token（用「」包住）且描述影響（成本/效能/定價/合作/合規/安全）\n"
    "Q3:\n"
    "- 第 1 條，至少 12 字，含錨點（token 或數字或專名）\n"
    "- 第 2 條，至少 12 字，含錨點（token 或數字或專名）\n"
    "- 第 3 條，至少 12 字，含錨點（token 或數字或專名）\n"
    "Proof: 此行必須逐字照抄 user 訊息中「Proof 行」的完整內容，不得改寫、不得增減任何字元或空白。\n"
    "注意：禁止在輸出中使用任何角括號、大括號、方括號或佔位符字串。\n"
    "6) 禁止輸出「現有策略與資源配置」與「資源配置」；若要表達請改用「資源分配」。"
)


@dataclass
class _ParsedOutput:
    q1: str
    q2: str
    q3_lines: List[str]
    proof: str


# ── Task B helpers ────────────────────────────────────────

def _norm_id(s: str) -> str:
    """Normalize source / date / proof strings: CRLF→LF, newlines→space,
    collapse whitespace, strip.  Used to eliminate trailing-whitespace / CRLF
    pollution before strict-equality Proof comparison."""
    s = (s or "")
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("\n", " ")
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()


# ── Task A helpers ────────────────────────────────────────

def _normalize_ws(s: str) -> str:
    """Collapse CRLF/CR → LF, multiple spaces/tabs → single space,
    multiple newlines → single newline, then strip."""
    s = (s or "")
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{2,}", "\n", s)
    return s.strip()


_RE_SENT_TERM = re.compile(r"[。！？!?]+")
_RE_SECONDARY_SPLIT = re.compile(r"[；;：:\n]")


def _split_sentences_lenient(s: str) -> List[str]:
    """Split s into sentence fragments, tolerating newline/semicolon/colon
    delimiters in addition to standard CJK/ASCII sentence terminators.

    Primary: split on 。！？!? (sentence-final punctuation).
      - If primary yields >= 2 parts → return primary (caller handles merge).
    Secondary (only when primary yields < 2): also split on ；;：:\n.
    Empty fragments are discarded; all fragments are stripped.
    """
    s = _normalize_ws(s)
    if not s:
        return []

    # Primary split on sentence terminators
    primary = [p.strip() for p in _RE_SENT_TERM.split(s) if p.strip()]

    # If primary gives >= 2 parts, return them; _coerce_to_two_sentences merges if > 2
    if len(primary) >= 2:
        return primary

    # Primary gave 0 or 1 part → try secondary delimiters (；;：:\n)
    secondary = [p.strip() for p in _RE_SECONDARY_SPLIT.split(s) if p.strip()]
    # Strip any residual sentence-terminal chars from each secondary part
    secondary = [_RE_SENT_TERM.sub("", p).strip() for p in secondary]
    secondary = [p for p in secondary if p]

    if len(secondary) >= 2:
        return secondary

    # Fall back to whatever we have
    return primary if primary else secondary


def _coerce_to_two_sentences(s: str) -> Optional[Tuple[str, str]]:
    """Return exactly two sentence strings extracted from s, or None if
    impossible.  Merge rules are fixed and auditable.

    parts == 2 → return as-is.
    parts > 2  → s1 = parts[0], s2 = join(parts[1:]).
    parts == 1 → try comma-split (once), then give up → None.
    parts == 0 → None.
    """
    parts = _split_sentences_lenient(s)

    if len(parts) == 2:
        return (_normalize_ws(parts[0]), _normalize_ws(parts[1]))

    if len(parts) > 2:
        s1 = _normalize_ws(parts[0])
        s2 = _normalize_ws(" ".join(parts[1:]))
        return (s1, s2)

    if len(parts) == 1:
        # Last-resort: split on first 、，, occurrence
        comma_parts = re.split(r"[，,、]", parts[0], maxsplit=1)
        comma_parts = [_normalize_ws(p) for p in comma_parts if _normalize_ws(p)]
        if len(comma_parts) == 2:
            return (comma_parts[0], comma_parts[1])
        # Still only 1 part → cannot form two sentences
        return None

    return None  # parts == 0


# ── Unchanged helpers ─────────────────────────────────────

def _check_health() -> bool:
    try:
        req = urllib.request.Request(HEALTH_URL, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def _sync_post(payload: dict) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        obj = json.loads(raw)
    # Safety guard: ensure the expected structure exists so callers get a
    # retriable ValueError instead of an unhandled KeyError/IndexError/TypeError.
    try:
        content = obj["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"Unexpected llama-server response structure: {exc!r}") from exc
    if not isinstance(content, str):
        raise ValueError(f"llama-server 'content' is not a string: {type(content)}")
    return obj


def _normalize_claude(text: str) -> str:
    if not text:
        return text
    text = text.replace("Claude (Anthropic)", "Claude（Anthropic）")
    text = text.replace("Claude(Anthropic)", "Claude（Anthropic）")
    for bad in _BAD_CLAUDE:
        text = text.replace(bad, "Claude（Anthropic）")
    return text


def _parse_output(text: str) -> Optional[_ParsedOutput]:
    m = _RE_PARSE.match((text or "").strip())
    if not m:
        return None
    q1 = (m.group("q1") or "").strip()
    q2 = (m.group("q2") or "").strip()
    q3 = (m.group("q3") or "").strip()
    proof = (m.group("proof") or "").strip()
    q3_lines = [ln.strip() for ln in q3.splitlines() if ln.strip()]
    return _ParsedOutput(q1=q1, q2=q2, q3_lines=q3_lines, proof=proof)


def _extract_quote_tokens(section_text: str) -> List[str]:
    return [t.strip() for t in _RE_QUOTES.findall(section_text or "") if t.strip()]


def _is_rich_quote(token: str) -> bool:
    """True when token qualifies as a rich quote (>= 20 chars, meaningful content).

    Criteria:
      - len >= 20
      - NOT all symbols / pure number
      - At least one of: has space (multi-word phrase), OR >= 4 CJK chars,
        OR mixed Latin + at least one non-symbol char
    """
    t = token.strip()
    if len(t) < 20:
        return False
    # Must contain at least one word char or CJK char
    if not re.search(r"[\w\u4e00-\u9fff]", t):
        return False
    # Reject pure numeric (possibly with $, %, ,, .)
    if re.fullmatch(r"[\d\s,.$%+\-/\\]+", t):
        return False
    has_space = " " in t
    zh_count = len(re.findall(r"[\u4e00-\u9fff]", t))
    has_en = bool(re.search(r"[A-Za-z]", t))
    has_non_sym = bool(re.search(r"[\w\u4e00-\u9fff]", t))
    return has_space or zh_count >= 4 or (has_en and has_non_sym)


def _bullet_text(line: str) -> Tuple[bool, str]:
    s = (line or "").strip()
    if s.startswith("-"):
        return True, s[1:].strip()
    if s.startswith("•"):
        return True, s[1:].strip()
    return False, s


def _len_no_space(s: str) -> int:
    return len(re.sub(r"\s+", "", s or ""))


def _validate_output(
    out_text: str, raw_text: str, source: str, date_yyyy_mm_dd: str
) -> Tuple[bool, List[str]]:
    reasons: List[str] = []

    if not out_text or not out_text.strip():
        return False, ["empty_output"]

    if _RE_ELLIPSIS.search(out_text):
        reasons.append("ellipsis_forbidden")

    if _RE_ANY_BRACES.search(out_text):
        reasons.append("braces_forbidden")

    for bad in _BAD_CLAUDE:
        if bad in out_text:
            reasons.append("bad_claude_transliteration")

    if "Claude" in out_text and "Claude（Anthropic）" not in out_text:
        reasons.append("claude_must_be_parenthesized")

    for gp in _GENERIC_PHRASES:
        if gp in out_text:
            reasons.append(f"generic_phrase_hit:{gp}")

    parsed = _parse_output(out_text)
    if not parsed:
        reasons.append("format_parse_failed")
        return False, reasons

    # ── Task B: Proof strict equality after _norm_id normalization ──
    expected_proof = _norm_id(f"證據：來源：{source}（{date_yyyy_mm_dd}）")
    parsed_proof_normed = _norm_id(parsed.proof)
    if parsed_proof_normed != expected_proof:
        reasons.append("proof_not_exact_match")

    raw_text = raw_text or ""

    q1_tokens = _extract_quote_tokens(parsed.q1)
    q2_tokens = _extract_quote_tokens(parsed.q2)
    if len(q1_tokens) < 1:
        reasons.append("q1_missing_quote_token")
    if len(q2_tokens) < 1:
        reasons.append("q2_missing_quote_token")

    for t in q1_tokens[:3]:
        if t not in raw_text:
            reasons.append("q1_quote_token_not_in_raw")
            break
    for t in q2_tokens[:3]:
        if t not in raw_text:
            reasons.append("q2_quote_token_not_in_raw")
            break

    # ── Rich quote validation: at least 1 token across Q1+Q2 must be rich ──
    # Rich = len >= 20, multi-word / 4+ CJK / mixed alpha, not pure number/symbol.
    rich_q1 = [t for t in q1_tokens if _is_rich_quote(t)]
    rich_q2 = [t for t in q2_tokens if _is_rich_quote(t)]
    if q1_tokens and not rich_q1:
        reasons.append("q1_quote_not_rich")
    if q2_tokens and not rich_q2:
        reasons.append("q2_quote_not_rich")
    # Hard failure: both Q1 and Q2 have tokens but none is rich
    if q1_tokens and q2_tokens and not rich_q1 and not rich_q2:
        reasons.append("no_rich_quote_in_q1q2")

    # ── Task A: Q1/Q2 sentence count via lenient coercion ──
    q1_pair = _coerce_to_two_sentences(parsed.q1)
    q2_pair = _coerce_to_two_sentences(parsed.q2)

    if q1_pair is None:
        reasons.append("q1_sentence_count_not_2:cannot_coerce")
    else:
        for i, sent in enumerate(q1_pair, 1):
            if _len_no_space(sent) > 80:
                reasons.append(f"q1_sentence_too_long:{i}")

    if q2_pair is None:
        reasons.append("q2_sentence_count_not_2:cannot_coerce")
    else:
        for i, sent in enumerate(q2_pair, 1):
            if _len_no_space(sent) > 80:
                reasons.append(f"q2_sentence_too_long:{i}")

    # Q3 must be exactly 3 bullets, each >=12 chars and has anchor-ish token
    if len(parsed.q3_lines) != 3:
        reasons.append(f"q3_bullet_count_not_3:{len(parsed.q3_lines)}")
    else:
        for i, ln in enumerate(parsed.q3_lines, 1):
            is_bullet, btxt = _bullet_text(ln)
            if not is_bullet:
                reasons.append(f"q3_line_not_bullet:{i}")
                continue
            if _len_no_space(btxt) < 12:
                reasons.append(f"q3_bullet_too_short:{i}")
            has_anchor = False
            if _RE_QUOTES.search(btxt):
                has_anchor = True
            if re.search(r"\b\d+(?:\.\d+)?(?:%|x|B|M|K|day|hr|ms|s)?\b", btxt, re.I):
                has_anchor = True
            if re.search(r"\b[A-Z][A-Za-z0-9_.-]{2,}\b", btxt):
                has_anchor = True
            if not has_anchor:
                reasons.append(f"q3_missing_anchor:{i}")

    return (len(reasons) == 0), reasons


def _build_user_content(raw_text: str, source: str, date_yyyy_mm_dd: str) -> str:
    truncated = (raw_text or "")[:MAX_INPUT_CHARS]
    return (
        f"來源：{source}\n"
        f"日期：{date_yyyy_mm_dd}\n\n"
        f"【原文開始】\n{truncated}\n【原文結束】\n\n"
        f"請嚴格輸出完整四段（Q1/Q2/Q3/Proof）。\n"
        f"Proof 行必須完全等於：證據：來源：{source}（{date_yyyy_mm_dd}）\n"
        f"Q1/Q2 的「」token 必須逐字複製自原文，且 token 必須出現在原文中。"
    )


def _build_repair_user_content(
    raw_text: str,
    source: str,
    date_yyyy_mm_dd: str,
    prev_output: str,
    reasons: List[str],
) -> str:
    truncated = (raw_text or "")[:MAX_INPUT_CHARS]
    reason_str = ", ".join(reasons[:10])
    return (
        f"你上一版輸出違規（{reason_str}），必須完全重做。\n"
        f"再次強調：不得省略號；不得輸出任何 {{ }}；Q1/Q2 必須各 2 句且<=80字；"
        f"Q1/Q2 各至少 1 個「原文逐字token」且 token 必須存在原文；"
        f"token 必須是 rich quote（>=20字、含空格或多詞、非純數字）；"
        f"Q3 必須三條且每條>=12字；"
        f"Proof 必須完全等於：證據：來源：{source}（{date_yyyy_mm_dd}）。\n\n"
        f"來源：{source}\n日期：{date_yyyy_mm_dd}\n\n"
        f"【原文開始】\n{truncated}\n【原文結束】\n\n"
        f"【你上一版輸出（僅供對照）】\n{prev_output}\n\n"
        f"現在請重新輸出（只允許四段格式，不得多字）。"
    )


class LlamaCppServer:
    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        atexit.register(self.stop)

    async def start(self) -> None:
        if await asyncio.to_thread(_check_health):
            logger.info("llama-server already healthy on port 8080; reusing.")
            return

        if not os.path.exists(LLAMA_SERVER_EXE):
            raise FileNotFoundError(f"llama-server.exe not found: {LLAMA_SERVER_EXE}")
        if not os.path.exists(MODEL_GGUF):
            raise FileNotFoundError(f"Model gguf not found: {MODEL_GGUF}")

        cmd = [
            LLAMA_SERVER_EXE,
            "-m", MODEL_GGUF,
            "-c", "2048",
            "-ngl", "33",
            "--port", "8080",
            "--host", "127.0.0.1",
        ]
        creationflags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        )
        logger.info("Launching llama-server...")
        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )

        deadline = time.monotonic() + SERVER_READY_TIMEOUT_S
        while time.monotonic() < deadline:
            if await asyncio.to_thread(_check_health):
                logger.info("llama-server is healthy.")
                return
            await asyncio.sleep(SERVER_HEALTH_POLL_S)

        raise RuntimeError("llama-server did not become healthy within timeout.")

    def stop(self) -> None:
        if self._proc is None:
            return
        try:
            self._proc.kill()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning("llama-server wait(timeout=5) expired; process may linger.")
            logger.info("llama-server stopped.")
        except Exception as exc:
            logger.warning("Error stopping llama-server: %s", exc)
        finally:
            self._proc = None


async def generate_bbc_news(raw_text: str, source: str, date_yyyy_mm_dd: str) -> str:
    raw_text = raw_text or ""
    # Task B: normalize inputs at entry point to prevent CRLF/whitespace pollution
    source = _norm_id(source or "unknown")
    date_yyyy_mm_dd = _norm_id(date_yyyy_mm_dd or "1970-01-01")

    base_payload = {"model": "qwen", "temperature": 0.0, "max_tokens": 800, "stream": False}
    prev_output = ""
    last_exc: Optional[Exception] = None

    for attempt in range(3):
        try:
            if attempt == 0:
                user_content = _build_user_content(raw_text, source, date_yyyy_mm_dd)
            else:
                _, reasons_prev = _validate_output(
                    prev_output, raw_text, source, date_yyyy_mm_dd
                )
                user_content = _build_repair_user_content(
                    raw_text, source, date_yyyy_mm_dd, prev_output, reasons_prev
                )

            payload = dict(base_payload)
            payload["messages"] = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ]

            result = await asyncio.to_thread(_sync_post, payload)
            content = (
                result.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "") or ""
            ).strip()
            content = _normalize_claude(content)

            ok, reasons = _validate_output(content, raw_text, source, date_yyyy_mm_dd)
            if ok:
                return content

            prev_output = content
            logger.warning(
                "LLM output invalid (attempt %d/3): %s", attempt + 1, ", ".join(reasons)
            )
            await asyncio.sleep(1.5 + attempt)

        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code >= 500 and attempt < 2:
                logger.warning("LLM HTTP %s on attempt %d/3; retrying.", exc.code, attempt + 1)
                await asyncio.sleep(2.5 + attempt)
                continue
            raise
        except urllib.error.URLError as exc:
            last_exc = exc
            reason = getattr(exc, "reason", None)
            if isinstance(reason, (socket.timeout, TimeoutError)) and attempt < 2:
                logger.warning("LLM timeout on attempt %d/3; retrying.", attempt + 1)
                await asyncio.sleep(2.5 + attempt)
                continue
            raise
        except (socket.timeout, TimeoutError) as exc:
            last_exc = exc
            if attempt < 2:
                logger.warning("LLM timeout on attempt %d/3; retrying.", attempt + 1)
                await asyncio.sleep(2.5 + attempt)
                continue
            raise
        except Exception as exc:
            last_exc = exc
            if attempt < 2:
                logger.warning(
                    "LLM error on attempt %d/3: %s; retrying.", attempt + 1, exc
                )
                await asyncio.sleep(2.0 + attempt)
                continue
            break

    if prev_output:
        _, reasons = _validate_output(prev_output, raw_text, source, date_yyyy_mm_dd)
        raise RuntimeError(
            f"generate_bbc_news failed strict validation after retries: {reasons}"
        )
    if last_exc is not None:
        raise RuntimeError(
            f"generate_bbc_news failed after retries: {last_exc}"
        ) from last_exc
    raise RuntimeError("generate_bbc_news failed: unknown error")


# ── Task C: tiny self-check (no network, no server) ──────

if __name__ == "__main__":
    # --- _coerce_to_two_sentences tests ---

    # 1) Standard 。 terminator → 2 sentences
    r1 = _coerce_to_two_sentences("OpenAI 發布新模型。效能提升 30%。")
    assert r1 is not None and len(r1) == 2, f"case1 failed: {r1}"

    # 2) Newline as sentence delimiter (no 。)
    r2 = _coerce_to_two_sentences("第一句內容說明了事件背景\n第二句說明了影響範圍")
    assert r2 is not None and len(r2) == 2, f"case2 failed: {r2}"

    # 3) Semicolon delimiter
    r3 = _coerce_to_two_sentences("成本降低了 40%；競爭對手面臨壓力")
    assert r3 is not None and len(r3) == 2, f"case3 failed: {r3}"

    # 4) More than 2 primary parts → merge tail into s2
    r4 = _coerce_to_two_sentences("事件背景說明。影響分析。後續展望。")
    assert r4 is not None and len(r4) == 2, f"case4 failed: {r4}"
    assert r4[0] == "事件背景說明", f"case4 s1 wrong: {r4[0]}"

    # 5) English mixed sentence with no terminator → comma fallback → two parts
    r5 = _coerce_to_two_sentences(
        "Claude（Anthropic）released a new model, the performance doubled"
    )
    assert r5 is not None and len(r5) == 2, f"case5 failed: {r5}"

    # 6) Truly single unsplittable string → None
    r6 = _coerce_to_two_sentences("一句話沒有任何分隔符無法拆成兩句")
    assert r6 is None, f"case6 expected None got: {r6}"

    # --- _norm_id tests ---

    # CRLF in source
    assert _norm_id("OpenAI\r\n") == "OpenAI", f"norm_id crlf source failed"

    # Trailing spaces in date
    assert _norm_id("2025-01-01  ") == "2025-01-01", f"norm_id trailing space failed"

    # Proof line with CRLF pollution
    raw_proof = "證據：來源：OpenAI（2025-01-01）\r\n"
    expected = "證據：來源：OpenAI（2025-01-01）"
    assert _norm_id(raw_proof) == _norm_id(expected), f"norm_id proof failed"

    # Multiple internal spaces in source name
    assert _norm_id("BBC  中文") == "BBC 中文", f"norm_id multi-space failed"

    print("All self-checks passed.")
