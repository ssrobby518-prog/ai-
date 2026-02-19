"""Z0 Collector — free, online AI-news fetcher using stdlib only.

Fetches RSS/Atom from official feeds, community feeds, GitHub release/commit
Atom feeds, and Google News RSS search queries.  Writes results to:

  <outdir>/latest.jsonl      — UTF-8, one JSON object per line
  <outdir>/latest.meta.json  — summary stats

Usage:
    python core/z0_collector.py --config config/z0_sources.json --outdir data/raw/z0
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ATOM_NS = "http://www.w3.org/2005/Atom"
_MEDIA_NS = "http://search.yahoo.com/mrss/"
_DC_NS = "http://purl.org/dc/elements/1.1/"

_HIGH_VALUE_PLATFORMS = frozenset({
    "openai", "anthropic", "nvidia", "huggingface", "deepmind",
    "google", "meta", "microsoft", "deepseek", "aws",
})
_MED_VALUE_PLATFORMS = frozenset({
    "arxiv", "techcrunch", "venturebeat", "theverge", "mittr",
    "hackernews", "infoq", "36kr", "wired",
})
_COMM_PLATFORMS = frozenset({"reddit", "youtube", "huggingface_forum"})

_AI_KW_HIGH = [
    "release", "launch", "model", "agent", "benchmark", "open-source",
    "weights", "gpt", "claude", "gemini", "llm", "inference", "reasoning",
    "multimodal", "rag", "fine-tun", "transformer", "foundation model",
    "large language", "generative", "deepseek", "qwen", "llama",
]
_AI_KW_LOW = [
    "ai", "machine learning", "deep learning", "neural", "dataset",
    "paper", "research", "algorithm",
]

# ---------------------------------------------------------------------------
# HTML stripping (stdlib only)
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_html(text: str) -> str:
    """Remove HTML tags and unescape entities."""
    if not text:
        return ""
    cleaned = _TAG_RE.sub(" ", text)
    cleaned = html.unescape(cleaned)
    return _WS_RE.sub(" ", cleaned).strip()


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------

def _item_id(title: str, url: str) -> str:
    payload = f"{title.strip()}|{url.strip()}"
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# RSS / Atom parsing (stdlib xml.etree only)
# ---------------------------------------------------------------------------

def _ns(tag: str, ns: str) -> str:
    return f"{{{ns}}}{tag}"


def _first_text(element: ET.Element, *tags: str) -> str:
    """Return text of the first matching child tag (plain or namespaced)."""
    for tag in tags:
        child = element.find(tag)
        if child is not None and child.text:
            return child.text.strip()
    return ""


def _parse_pubdate(raw: str) -> str:
    """Parse RSS pubDate or Atom published/updated into ISO8601 UTC string."""
    if not raw:
        return ""
    raw = raw.strip()
    # Try ISO-ish first (Atom)
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(raw[:25], fmt[:len(fmt)])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()
        except ValueError:
            pass
    # Try RFC 2822 (RSS): "Wed, 19 Feb 2026 09:00:00 +0000"
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(raw)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        pass
    return ""


def _parse_rss_items(root: ET.Element, feed_cfg: dict, max_items: int) -> list[dict]:
    """Parse RSS 2.0 / RDF channel items."""
    channel = root.find("channel")
    container = channel if channel is not None else root
    entries = container.findall("item") or root.findall(".//item")
    results = []
    for entry in entries[:max_items]:
        title = _strip_html(
            _first_text(entry, "title")
        )
        link = (
            _first_text(entry, "link")
            or _first_text(entry, "guid")
            or _first_text(entry, _ns("link", _ATOM_NS))
        )
        if not link:
            # <link> in RSS 2.0 may have no text but be a self-closing tag
            link_el = entry.find("link")
            if link_el is not None:
                link = (link_el.text or "").strip()
        pub_raw = (
            _first_text(entry, "pubDate")
            or _first_text(entry, "dc:date")
            or _first_text(entry, _ns("date", _DC_NS))
            or _first_text(entry, "updated")
        )
        summary = _strip_html(
            _first_text(entry, "description")
            or _first_text(entry, "summary")
            or _first_text(entry, _ns("summary", _ATOM_NS))
        )
        if title and link:
            results.append({
                "title": title,
                "url": link,
                "published_raw": pub_raw,
                "summary": summary[:500],
            })
    return results


def _parse_atom_items(root: ET.Element, feed_cfg: dict, max_items: int) -> list[dict]:
    """Parse Atom 1.0 feed entries."""
    ns = _ATOM_NS
    entries = root.findall(_ns("entry", ns)) or root.findall("entry")
    results = []
    for entry in entries[:max_items]:
        title = _strip_html(
            _first_text(entry, _ns("title", ns))
            or _first_text(entry, "title")
        )
        # <link rel="alternate" href="...">
        link = ""
        for link_el in entry.findall(_ns("link", ns)) + entry.findall("link"):
            rel = link_el.get("rel", "alternate")
            if rel in ("alternate", ""):
                link = link_el.get("href", "")
                if link:
                    break
        if not link:
            link = _first_text(entry, _ns("id", ns)) or _first_text(entry, "id")

        pub_raw = (
            _first_text(entry, _ns("published", ns))
            or _first_text(entry, _ns("updated", ns))
            or _first_text(entry, "published")
            or _first_text(entry, "updated")
        )
        summary = _strip_html(
            _first_text(entry, _ns("summary", ns))
            or _first_text(entry, _ns("content", ns))
            or _first_text(entry, "summary")
            or _first_text(entry, "content")
        )
        if title and link:
            results.append({
                "title": title,
                "url": link,
                "published_raw": pub_raw,
                "summary": summary[:500],
            })
    return results


def parse_feed(xml_text: str, feed_cfg: dict, max_items: int = 30) -> list[dict]:
    """Parse RSS 2.0 or Atom 1.0 XML.  Returns list of raw item dicts.

    This is a pure function — no I/O — to allow easy unit testing.
    """
    if not xml_text or not xml_text.strip():
        return []
    try:
        # Strip XML declaration encoding conflicts that can trip ET
        xml_bytes = xml_text.encode("utf-8", errors="replace")
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        try:
            # Try stripping BOM / invalid chars
            cleaned = xml_text.lstrip("\ufeff\ufffe")
            root = ET.fromstring(cleaned.encode("utf-8", errors="replace"))
        except ET.ParseError:
            return []

    tag_lower = root.tag.lower()
    if "feed" in tag_lower or root.tag == _ns("feed", _ATOM_NS):
        raw_items = _parse_atom_items(root, feed_cfg, max_items)
    else:
        raw_items = _parse_rss_items(root, feed_cfg, max_items)

    platform = feed_cfg.get("platform", "unknown")
    feed_name = feed_cfg.get("name", "unknown")
    feed_url = feed_cfg.get("url", "")
    tag = feed_cfg.get("tag", "unknown")
    now = datetime.now(timezone.utc)
    collected_at = now.isoformat()

    items = []
    for raw in raw_items:
        url = raw["url"].strip()
        if not url.startswith("http"):
            continue
        title = raw["title"].strip()
        if not title:
            continue
        published_at = _parse_pubdate(raw["published_raw"])
        item: dict[str, Any] = {
            "id": _item_id(title, url),
            "title": title,
            "url": url,
            "domain": _extract_domain(url),
            "published_at": published_at or None,
            "summary": raw.get("summary", ""),
            "content_text": "",
            "frontier_score": 0,
            "source": {
                "platform": platform,
                "feed_name": feed_name,
                "feed_url": feed_url,
                "tag": tag,
            },
            "collected_at": collected_at,
        }
        item["frontier_score"] = compute_frontier_score(item)
        items.append(item)
    return items


# ---------------------------------------------------------------------------
# Frontier score
# ---------------------------------------------------------------------------

def compute_frontier_score(item: dict) -> int:
    """0–100 composite score: recency(0-50) + platform(0-20) + keywords(0-30)."""
    score = 0
    now = datetime.now(timezone.utc)

    # Recency (0-50)
    pub = item.get("published_at")
    if pub:
        try:
            dt = datetime.fromisoformat(str(pub).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age_h = (now - dt).total_seconds() / 3600.0
            if age_h < 6:
                score += 50
            elif age_h < 24:
                score += 45
            elif age_h < 48:
                score += 38
            elif age_h < 72:
                score += 30
            elif age_h < 168:
                score += 20
            else:
                score += 10
        except Exception:
            score += 20
    else:
        score += 20

    # Platform bonus (0-20)
    platform = str(item.get("source", {}).get("platform", "")).lower()
    if platform in _HIGH_VALUE_PLATFORMS:
        score += 20
    elif platform in _MED_VALUE_PLATFORMS:
        score += 12
    elif platform in _COMM_PLATFORMS:
        score += 8
    else:
        score += 4

    # Keyword bonus (0-30)
    text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    kw_bonus = 0
    for kw in _AI_KW_HIGH:
        if kw in text:
            kw_bonus += 3
    for kw in _AI_KW_LOW:
        if kw in text:
            kw_bonus += 1
    score += min(kw_bonus, 30)

    return min(100, score)


# ---------------------------------------------------------------------------
# Domain extraction
# ---------------------------------------------------------------------------

def _extract_domain(url: str) -> str:
    try:
        parsed = urllib.parse.urlparse(url)
        host = parsed.netloc.lower()
        return host.lstrip("www.")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# HTTP fetch (stdlib urllib only)
# ---------------------------------------------------------------------------

def _fetch_url(url: str, timeout: int = 15, user_agent: str = "AI-Intel-Z0/1.0") -> str | None:
    """Fetch URL text with urllib.request.  Returns None on any error."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "application/rss+xml, application/atom+xml, text/xml, */*",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            # Detect charset from Content-Type header
            ct = resp.headers.get("Content-Type", "")
            charset = "utf-8"
            m = re.search(r"charset=([^\s;]+)", ct, re.IGNORECASE)
            if m:
                charset = m.group(1).strip('"').strip("'")
            try:
                return raw.decode(charset, errors="replace")
            except LookupError:
                return raw.decode("utf-8", errors="replace")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# GitHub Atom feed builders
# ---------------------------------------------------------------------------

def _github_feed_url(owner: str, repo: str, feed_type: str) -> str:
    if feed_type == "releases":
        return f"https://github.com/{owner}/{repo}/releases.atom"
    if feed_type == "commits":
        return f"https://github.com/{owner}/{repo}/commits/main.atom"
    if feed_type == "discussions":
        return f"https://github.com/{owner}/{repo}/discussions.atom"
    return ""


# ---------------------------------------------------------------------------
# Google News RSS URL builder
# ---------------------------------------------------------------------------

def _gnews_url(query: str, locale: dict) -> str:
    hl = locale.get("hl", "en-US")
    gl = locale.get("gl", "US")
    ceid = locale.get("ceid", "US:en")
    q = urllib.parse.quote_plus(query)
    return f"https://news.google.com/rss/search?q={q}&hl={hl}&gl={gl}&ceid={ceid}"


# ---------------------------------------------------------------------------
# Main collection logic
# ---------------------------------------------------------------------------

def collect_all(config_path: Path, outdir: Path) -> dict:
    """Run full Z0 collection.  Returns meta dict.  Never raises."""
    outdir.mkdir(parents=True, exist_ok=True)

    try:
        cfg_text = config_path.read_text(encoding="utf-8")
        config = json.loads(cfg_text)
    except Exception as exc:
        print(f"[Z0] ERROR: cannot load config {config_path}: {exc}")
        return _write_empty_output(outdir)

    coll_cfg = config.get("collector", {})
    timeout = int(coll_cfg.get("http_timeout_sec", 15))
    delay_ms = int(coll_cfg.get("polite_delay_ms", 600))
    max_per_feed = int(coll_cfg.get("max_items_per_feed", 30))
    user_agent = str(coll_cfg.get("user_agent", "AI-Intel-Z0/1.0"))
    locale = coll_cfg.get("locale", {"hl": "en-US", "gl": "US", "ceid": "US:en"})

    all_items: list[dict] = []
    seen_ids: set[str] = set()

    def _process_feed(feed_cfg: dict, xml_text: str | None) -> int:
        if not xml_text:
            return 0
        items = parse_feed(xml_text, feed_cfg, max_items=max_per_feed)
        added = 0
        for item in items:
            if item["id"] not in seen_ids:
                seen_ids.add(item["id"])
                all_items.append(item)
                added += 1
        return added

    def _fetch_and_add(feed_cfg: dict, url: str) -> None:
        time.sleep(delay_ms / 1000.0)
        xml_text = _fetch_url(url, timeout=timeout, user_agent=user_agent)
        n = _process_feed(feed_cfg, xml_text)
        status = "ok" if xml_text else "err"
        print(f"[Z0] {status:3s} +{n:3d}  {feed_cfg.get('name', url)[:60]}")

    # Official feeds
    for feed_cfg in config.get("official_feeds", []):
        _fetch_and_add(feed_cfg, feed_cfg["url"])

    # Community feeds
    for feed_cfg in config.get("community_feeds", []):
        _fetch_and_add(feed_cfg, feed_cfg["url"])

    # GitHub Atom feeds
    gh_watch = config.get("github_watch", {})
    feed_types = gh_watch.get("feeds", ["releases"])
    for repo_cfg in gh_watch.get("repos", []):
        owner = repo_cfg["owner"]
        repo = repo_cfg["repo"]
        platform = repo_cfg.get("platform", "github")
        for ft in feed_types:
            url = _github_feed_url(owner, repo, ft)
            if not url:
                continue
            feed_cfg = {
                "name": f"GitHub {owner}/{repo} [{ft}]",
                "url": url,
                "platform": platform,
                "tag": f"github_{ft}",
            }
            _fetch_and_add(feed_cfg, url)

    # Google News queries
    for q_cfg in config.get("google_news_queries", []):
        query = q_cfg.get("q", "")
        if not query:
            continue
        url = _gnews_url(query, locale)
        feed_cfg = {
            "name": f"GNews: {query[:50]}",
            "url": url,
            "platform": "google_news",
            "tag": q_cfg.get("tag", "gnews"),
        }
        _fetch_and_add(feed_cfg, url)

    # Build meta
    now_iso = datetime.now(timezone.utc).isoformat()
    by_platform: dict[str, int] = {}
    by_feed: dict[str, int] = {}
    for item in all_items:
        p = item["source"]["platform"]
        by_platform[p] = by_platform.get(p, 0) + 1
        fn = item["source"]["feed_name"]
        by_feed[fn] = by_feed.get(fn, 0) + 1

    frontier_ge_70 = sum(1 for it in all_items if it["frontier_score"] >= 70)
    frontier_ge_85 = sum(1 for it in all_items if it["frontier_score"] >= 85)

    meta = {
        "collected_at": now_iso,
        "total_items": len(all_items),
        "by_platform": by_platform,
        "by_feed": by_feed,
        "frontier_ge_70": frontier_ge_70,
        "frontier_ge_85": frontier_ge_85,
    }

    # Write JSONL
    jsonl_path = outdir / "latest.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as fh:
        for item in all_items:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")

    # Write meta
    meta_path = outdir / "latest.meta.json"
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"[Z0] Done. total={len(all_items)} frontier_ge_70={frontier_ge_70} frontier_ge_85={frontier_ge_85}")
    print(f"[Z0] Output: {jsonl_path}")
    return meta


def _write_empty_output(outdir: Path) -> dict:
    now_iso = datetime.now(timezone.utc).isoformat()
    meta = {
        "collected_at": now_iso,
        "total_items": 0,
        "by_platform": {},
        "by_feed": {},
        "frontier_ge_70": 0,
        "frontier_ge_85": 0,
        "error": "collection_failed",
    }
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "latest.jsonl").write_text("", encoding="utf-8")
    (outdir / "latest.meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return meta


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _main() -> None:
    parser = argparse.ArgumentParser(description="Z0 AI-news collector (stdlib only)")
    parser.add_argument("--config", required=True, help="Path to z0_sources.json")
    parser.add_argument("--outdir", required=True, help="Output directory for JSONL + meta")
    args = parser.parse_args()

    config_path = Path(args.config)
    outdir = Path(args.outdir)

    if not config_path.exists():
        print(f"[Z0] ERROR: config not found: {config_path}", file=sys.stderr)
        sys.exit(1)

    meta = collect_all(config_path, outdir)
    if meta.get("error"):
        sys.exit(1)


if __name__ == "__main__":
    _main()
