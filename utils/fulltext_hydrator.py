"""utils/fulltext_hydrator.py — stdlib-only full-text article hydrator.

Fetches publisher HTML and extracts clean article text. Handles Google News redirect URLs.
Writes outputs/fulltext_hydrator.meta.json after batch processing.

API:
  hydrate_fulltext(url, timeout_s=8) -> dict
  hydrate_items_batch(items)         -> list[items]  (mutates in-place)

stdlib only: urllib.request, html.parser, re, concurrent.futures, json, pathlib
"""
from __future__ import annotations

import base64
import json
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
_HEADERS = {
    "User-Agent": _UA,
    "Accept-Language": "en-US,en;q=0.9,zh-TW;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "identity",
}

MAX_HTML_READ = 300_000       # bytes
MAX_BODY_CHARS = 12_000       # chars in full_text output
_FULLTEXT_OK_MIN = 300        # chars: status="ok" only when >= this
_ENRICH_MIN = 300             # chars: enrich item.body only when fulltext_len >= this
_ENRICH_CAP = 5_000           # chars: max full_text slice appended to item.body

_GOOGLE_DOMAINS = frozenset({"news.google.com", "www.news.google.com", "google.com"})

_JS_SIGNALS = (
    "enable javascript",
    "javascript is required",
    "javascript required",
    "please turn on javascript",
    "please enable javascript",
)

_UI_GARBAGE = (
    "sign in to", "sign up", "subscribe", "log in", "login",
    "cookie policy", "privacy policy", "terms of service",
    "terms of use", "please enable", "advertisement",
    "newsletter", "follow us on", "share this article",
)


# ---------------------------------------------------------------------------
# HTML parser
# ---------------------------------------------------------------------------

class _ArticleParser(HTMLParser):
    """
    Extract <p> text prioritising <article>/<main> regions.
    Also collects meta-refresh URL, canonical link, and external hrefs.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_article: int = 0
        self._in_main: int = 0
        self._in_skip: int = 0   # script / style / nav / footer / header / aside
        self._in_p: bool = False
        self._article_ps: list[str] = []
        self._all_ps: list[str] = []
        self._buf: list[str] = []
        self.meta_refresh: str | None = None
        self.canonical: str | None = None
        self.ext_links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple]) -> None:
        ad = {k.lower(): (v or "") for k, v in attrs}
        tag = tag.lower()
        if tag == "article":
            self._in_article += 1
        elif tag == "main":
            self._in_main += 1
        elif tag in ("script", "style", "nav", "footer", "header", "aside", "form"):
            self._in_skip += 1
        elif tag == "p" and self._in_skip == 0:
            self._in_p = True
            self._buf = []
        elif tag == "meta":
            heq = ad.get("http-equiv", "").lower()
            if heq == "refresh":
                m = re.search(r"url\s*=\s*['\"]?([^'\";\s>]+)", ad.get("content", ""), re.IGNORECASE)
                if m:
                    self.meta_refresh = m.group(1).strip()
        elif tag == "link":
            if "canonical" in ad.get("rel", "").lower():
                self.canonical = ad.get("href", "")
        elif tag == "a":
            href = ad.get("href", "")
            if href.startswith("https://"):
                self.ext_links.append(href)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "article":
            self._in_article = max(0, self._in_article - 1)
        elif tag == "main":
            self._in_main = max(0, self._in_main - 1)
        elif tag in ("script", "style", "nav", "footer", "header", "aside", "form"):
            self._in_skip = max(0, self._in_skip - 1)
        elif tag == "p" and self._in_p:
            self._in_p = False
            text = " ".join(self._buf).strip()
            if text:
                if self._in_article > 0 or self._in_main > 0:
                    self._article_ps.append(text)
                else:
                    self._all_ps.append(text)
            self._buf = []

    def handle_data(self, data: str) -> None:
        if self._in_p and self._in_skip == 0:
            self._buf.append(data)

    def best_paragraphs(self) -> list[str]:
        return self._article_ps if self._article_ps else self._all_ps


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_google_domain(url: str) -> bool:
    try:
        netloc = urlparse(url).netloc.lower().lstrip("www.")
        return netloc in ("news.google.com", "google.com")
    except Exception:
        return False


def _is_external(url: str) -> bool:
    try:
        netloc = urlparse(url).netloc.lower()
        return bool(netloc) and netloc not in _GOOGLE_DOMAINS
    except Exception:
        return False


def _fetch_html(url: str, timeout_s: int) -> tuple[str, str]:
    """Fetch URL (follows HTTP redirects), return (html_text, final_url)."""
    req = Request(url, headers=_HEADERS)
    with urlopen(req, timeout=timeout_s) as resp:
        final_url = resp.geturl()
        content_type = resp.headers.get("Content-Type", "")
        charset = "utf-8"
        m = re.search(r"charset\s*=\s*([^\s;\"']+)", content_type, re.IGNORECASE)
        if m:
            charset = m.group(1).strip().strip("\"'")
        raw = resp.read(MAX_HTML_READ)
        try:
            return raw.decode(charset, errors="replace"), final_url
        except (LookupError, UnicodeDecodeError):
            return raw.decode("utf-8", errors="replace"), final_url


def _extract_text(html: str) -> str:
    """Extract clean article text from HTML."""
    parser = _ArticleParser()
    try:
        parser.feed(html)
    except Exception:
        pass

    paragraphs = parser.best_paragraphs()
    clean: list[str] = []
    for p in paragraphs[:40]:
        p = re.sub(r"\s+", " ", p).strip()
        if len(p) < 40:
            continue
        p_low = p.lower()
        if any(tok in p_low for tok in _UI_GARBAGE):
            continue
        clean.append(p)

    text = "\n\n".join(clean)
    return text[:MAX_BODY_CHARS]


def _decode_gnews_rss_url(url: str) -> str:
    """Decode actual publisher URL from a GNews RSS base64-encoded article URL.

    GNews RSS links (news.google.com/rss/articles/CBMi...) embed the publisher URL
    in a base64url-encoded protobuf payload.  Python urllib cannot follow the JS
    redirect on the GNews landing page, but we can extract the URL directly from
    the binary payload without any HTTP request.

    Returns the decoded publisher URL, or "" if decoding fails.
    """
    try:
        m = re.search(r'/rss/articles/([^?&#\s]+)', url)
        if not m:
            return ""
        encoded = m.group(1)
        # Normalize base64url → base64, add padding
        encoded = encoded.replace('-', '+').replace('_', '/')
        padding = 4 - len(encoded) % 4
        if padding != 4:
            encoded += '=' * padding
        decoded = base64.b64decode(encoded)
        # The publisher URL is embedded as a UTF-8 string in the binary protobuf.
        # Scan for "https://" or "http://" sequence in the decoded bytes.
        found = re.search(rb'https?://[^\x00-\x1f\x80-\xff]{15,}', decoded)
        if found:
            candidate = found.group(0).decode('ascii', errors='replace').rstrip('\\')
            # Basic sanity: must be a real URL, not a google domain
            if candidate.startswith('http') and 'google.com' not in candidate[:30]:
                return candidate
    except Exception:
        pass
    return ""


def _resolve_google_news_url(html: str) -> str | None:
    """Extract publisher URL from Google News HTML."""
    parser = _ArticleParser()
    try:
        parser.feed(html)
    except Exception:
        pass
    if parser.meta_refresh and _is_external(parser.meta_refresh):
        return parser.meta_refresh
    if parser.canonical and _is_external(parser.canonical):
        return parser.canonical
    for href in parser.ext_links:
        if _is_external(href):
            return href
    return None


def _quick_zh_ratio(text: str) -> float:
    """Fast zh_ratio for internal use only."""
    if not text:
        return 0.0
    sample = text[:500]
    zh = sum(1 for c in sample if "\u4e00" <= c <= "\u9fff")
    asc = sum(1 for c in sample if c.isascii() and c.isalpha())
    total = zh + asc
    return zh / total if total else 0.0


def _get_logger():
    try:
        from utils.logger import get_logger  # type: ignore
        return get_logger()
    except Exception:
        import logging
        return logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public: single-URL hydration
# ---------------------------------------------------------------------------

def hydrate_fulltext(url: str, timeout_s: int = 8) -> dict:
    """
    Fetch full article text from URL.

    Returns:
      {
        "final_url": str,
        "status":    "ok" | "skip" | "fail",
        "full_text": str,
        "fulltext_len": int,
        "reason":    str,
      }

    Never raises.
    """
    result: dict = {
        "final_url": url or "",
        "status": "fail",
        "full_text": "",
        "fulltext_len": 0,
        "reason": "",
    }

    if not url or not url.startswith("http"):
        result["reason"] = "no_url"
        return result

    t0 = time.monotonic()

    try:
        # Phase 1: fetch URL (urllib follows HTTP 301/302 redirects)
        html, final_url = _fetch_html(url, timeout_s=max(3, timeout_s))
        result["final_url"] = final_url
        elapsed = time.monotonic() - t0

        # Phase 2: if still on Google domain, resolve to publisher.
        # First try direct base64 decode (avoids JS-redirect issue on GNews pages);
        # fall back to HTML-based extraction if that fails.
        if _is_google_domain(final_url):
            publisher_url = _decode_gnews_rss_url(url) or _resolve_google_news_url(html)
            remaining = max(1.0, timeout_s - elapsed - 0.5)
            if publisher_url and remaining > 1:
                try:
                    html2, final_url2 = _fetch_html(publisher_url, timeout_s=int(remaining))
                    html = html2
                    result["final_url"] = final_url2
                except Exception:
                    result["final_url"] = publisher_url  # best guess
            # fallthrough: extract from whatever html we have

        # Phase 3: JS-only detection (check first 4 KB)
        html_head = html[:4000].lower()
        if any(sig in html_head for sig in _JS_SIGNALS):
            result["status"] = "skip"
            result["reason"] = "js_only"
            return result

        # Phase 4: text extraction
        text = _extract_text(html)

        if len(text) < _FULLTEXT_OK_MIN:
            result["status"] = "fail"
            result["reason"] = "extract_too_short"
            return result

        result["status"] = "ok"
        result["full_text"] = text
        result["fulltext_len"] = len(text)
        result["reason"] = "ok"
        return result

    except HTTPError as exc:
        result["reason"] = f"http_{exc.code}"
        result["status"] = "fail"
        return result
    except URLError as exc:
        reason_str = str(getattr(exc, "reason", exc))
        if "timed out" in reason_str.lower() or "timeout" in reason_str.lower():
            result["reason"] = "timeout"
        else:
            result["reason"] = f"url_error:{reason_str[:50]}"
        result["status"] = "fail"
        return result
    except TimeoutError:
        result["reason"] = "timeout"
        result["status"] = "fail"
        return result
    except Exception as exc:
        result["reason"] = f"{type(exc).__name__}:{str(exc)[:50]}"
        result["status"] = "fail"
        return result


# ---------------------------------------------------------------------------
# Public: batch hydration
# ---------------------------------------------------------------------------

def hydrate_items_batch(
    items: list,
    timeout_s: int = 8,
    max_workers: int = 4,
    batch_timeout: int = 120,
) -> list:
    """
    Hydrate all items with full article text in parallel.

    For each item:
      - Calls hydrate_fulltext(item.url)
      - Sets attributes: full_text, fulltext_len, fulltext_status, final_url, fulltext_reason
      - Appends full_text to item.body when fulltext_len >= 300

    Writes outputs/fulltext_hydrator.meta.json.
    Returns the (mutated) items list.
    """
    if not items:
        _write_hydrator_meta([])
        return items

    log = _get_logger()
    t0 = time.monotonic()

    # Group items by URL to avoid redundant requests
    url_to_items: dict[str, list] = {}
    for item in items:
        url = (getattr(item, "url", "") or "").strip()
        if url and url.startswith("http"):
            url_to_items.setdefault(url, []).append(item)
        else:
            setattr(item, "full_text", "")
            setattr(item, "fulltext_len", 0)
            setattr(item, "fulltext_status", "fail")
            setattr(item, "final_url", "")
            setattr(item, "fulltext_reason", "no_url")

    # Sort URLs: process direct article sources first (iThome, Bloomberg, HuggingFace etc.)
    # before GNews/GitHub/arXiv which historically return short or unextractable content.
    # This ensures high-value direct-article URLs complete within batch_timeout budget.
    def _url_priority(u: str) -> int:
        try:
            netloc = urlparse(u).netloc.lower().lstrip("www.")
        except Exception:
            netloc = ""
        if netloc in ("news.google.com", "google.com"):
            return 3  # GNews: JS redirects, usually extract_too_short
        if "github.com" in netloc:
            return 2  # GitHub: short release notes
        if "arxiv.org" in netloc:
            return 2  # arXiv: only abstract (~450c)
        return 0      # Direct article domains: highest priority

    unique_urls = sorted(url_to_items.keys(), key=_url_priority)
    done: dict[str, dict] = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {executor.submit(hydrate_fulltext, u, timeout_s): u for u in unique_urls}
        remaining = max(5.0, batch_timeout - (time.monotonic() - t0))
        try:
            for future in as_completed(future_map, timeout=remaining):
                u = future_map[future]
                try:
                    done[u] = future.result()
                except Exception as exc:
                    done[u] = {
                        "final_url": u, "status": "fail",
                        "full_text": "", "fulltext_len": 0,
                        "reason": f"future_exc:{type(exc).__name__}",
                    }
        except TimeoutError:
            for fut, u in future_map.items():
                if u not in done:
                    fut.cancel()
                    done[u] = {
                        "final_url": u, "status": "fail",
                        "full_text": "", "fulltext_len": 0,
                        "reason": "batch_timeout",
                    }

    ok_count = 0
    for url, grp in url_to_items.items():
        res = done.get(url, {
            "final_url": url, "status": "fail",
            "full_text": "", "fulltext_len": 0, "reason": "not_completed",
        })
        full_text = res.get("full_text", "") or ""
        fulltext_len = res.get("fulltext_len", 0) or 0
        status = res.get("status", "fail")
        if status == "ok":
            ok_count += 1

        for item in grp:
            setattr(item, "full_text", full_text)
            setattr(item, "fulltext_len", fulltext_len)
            setattr(item, "fulltext_status", status)
            setattr(item, "final_url", res.get("final_url", url))
            setattr(item, "fulltext_reason", res.get("reason", ""))

            # Enrich item.body when full_text is substantial
            if fulltext_len >= _ENRICH_MIN:
                try:
                    original_body = getattr(item, "body", "") or ""
                    ft_slice = full_text[:_ENRICH_CAP]
                    if ft_slice not in original_body:
                        item.body = original_body + "\n\n" + ft_slice
                except Exception:
                    pass

    elapsed = time.monotonic() - t0
    log.info(
        "hydrate_items_batch: total=%d unique_urls=%d ok=%d elapsed=%.2fs",
        len(items), len(unique_urls), ok_count, elapsed,
    )

    _write_hydrator_meta(items)
    return items


# ---------------------------------------------------------------------------
# Meta writer
# ---------------------------------------------------------------------------

def _write_hydrator_meta(items: list, outdir: str | None = None) -> None:
    """Write outputs/fulltext_hydrator.meta.json."""
    try:
        root = Path(outdir) if outdir else Path(__file__).resolve().parent.parent / "outputs"
        root.mkdir(parents=True, exist_ok=True)

        events_total = len(items)
        ok_items = [i for i in items if getattr(i, "fulltext_status", "") == "ok"]
        fulltext_ok_count = len(ok_items)
        fulltext_applied = sum(
            1 for i in items if getattr(i, "fulltext_status", None) is not None
        )
        coverage_ratio = round(fulltext_ok_count / events_total, 3) if events_total else 0.0

        ok_lens = [getattr(i, "fulltext_len", 0) for i in ok_items]
        avg_fulltext_len = round(sum(ok_lens) / len(ok_lens)) if ok_lens else 0

        fail_reasons: Counter = Counter()
        for i in items:
            reason = getattr(i, "fulltext_reason", "")
            status = getattr(i, "fulltext_status", "")
            if status in ("fail", "skip") and reason:
                fail_reasons[reason[:40]] += 1

        notes_parts: list[str] = []
        if fulltext_ok_count == 0 and events_total > 0:
            notes_parts.append("all_fulltext_fail")
        all_zh = events_total > 0 and all(
            _quick_zh_ratio(getattr(i, "body", "") or "") > 0.40 for i in items
        )
        if all_zh:
            notes_parts.append("all_zh_source")

        samples = sorted(items, key=lambda x: getattr(x, "fulltext_len", 0), reverse=True)[:5]
        sample_dicts = [
            {
                "title": (getattr(i, "title", "") or "")[:80],
                "final_url": getattr(i, "final_url", getattr(i, "url", "")),
                "fulltext_len": getattr(i, "fulltext_len", 0),
                "status": getattr(i, "fulltext_status", ""),
                "reason": getattr(i, "fulltext_reason", ""),
            }
            for i in samples
        ]

        meta = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "events_total": events_total,
            "fulltext_applied": fulltext_applied,
            "fulltext_ok_count": fulltext_ok_count,
            "coverage_ratio": coverage_ratio,
            "avg_fulltext_len": avg_fulltext_len,
            "samples": sample_dicts,
            "fail_reasons_top": dict(fail_reasons.most_common(10)),
            "notes": " / ".join(notes_parts),
        }

        out_path = root / "fulltext_hydrator.meta.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

    except Exception:
        pass  # non-fatal
