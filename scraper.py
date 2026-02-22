# FILE: scraper.py
import asyncio
import html
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

GLOBAL_TIMEOUT = 120
REQUEST_TIMEOUT = 10
MAX_TEXT_LEN = 12000

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# ── 來源設定 ──────────────────────────────────────────────
# 每個 source dict 欄位：
#   type  : "youtube" | "bilibili" | "twitter" | "douyin" | "tiktok" | "xiaohongshu"
#   name  : 顯示名稱（注入 Proof）
#   ids   : list[str]  頻道 / 用戶 ID 或帳號名
#   nodes : list[str]  Nitter / RSSHub base URL 節點池（twitter/douyin/tiktok/xiaohongshu 用）
#
# 清單為空時 scrape_all() 正常回傳 []
SOURCES: List[Dict[str, Any]] = [
    # YouTube 範例（填入真實 video id 列表，或改為動態取得）
    # {
    #     "type": "youtube",
    #     "name": "OpenAI YouTube",
    #     "ids": ["dQw4w9WgXcQ"],   # video id 列表
    # },
    # B站範例
    # {
    #     "type": "bilibili",
    #     "name": "某 UP 主",
    #     "ids": ["123456789"],      # host_mid
    # },
    # Twitter / X 範例（走 Nitter RSS）
    # {
    #     "type": "twitter",
    #     "name": "elonmusk Twitter",
    #     "ids": ["elonmusk"],
    #     "nodes": [
    #         "https://nitter.net",
    #         "https://nitter.privacydev.net",
    #         "https://nitter.poast.org",
    #     ],
    # },
    # Douyin 範例（走 RSSHub）
    # {
    #     "type": "douyin",
    #     "name": "某抖音帳號",
    #     "ids": ["MS4wLjABAAAA..."],
    #     "nodes": ["https://rsshub.app"],
    # },
    # TikTok 範例（走 RSSHub）
    # {
    #     "type": "tiktok",
    #     "name": "某 TikTok 帳號",
    #     "ids": ["username"],
    #     "nodes": ["https://rsshub.app"],
    # },
    # 小紅書範例（走 RSSHub）
    # {
    #     "type": "xiaohongshu",
    #     "name": "某小紅書帳號",
    #     "ids": ["uid123"],
    #     "nodes": ["https://rsshub.app"],
    # },
]

_sem = asyncio.Semaphore(8)

_NOW_DATE = datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ── 底層 fetch ────────────────────────────────────────────

def _sync_fetch(url: str, extra_headers: Optional[Dict[str, str]] = None) -> bytes:
    h = dict(HEADERS)
    if extra_headers:
        h.update(extra_headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return resp.read()


async def _fetch(url: str, extra_headers: Optional[Dict[str, str]] = None) -> bytes:
    async with _sem:
        return await asyncio.to_thread(_sync_fetch, url, extra_headers)


async def _fetch_failover(urls: List[str],
                          extra_headers: Optional[Dict[str, str]] = None) -> Optional[bytes]:
    for url in urls:
        try:
            return await _fetch(url, extra_headers)
        except urllib.error.HTTPError as exc:
            logger.warning("Failover: %s => HTTP %s", url, exc.code)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.warning("Failover: %s => %s", url, exc)
    return None


# ── XML 工具 ──────────────────────────────────────────────

def _strip_xml_tags(xml_bytes: bytes) -> str:
    try:
        root = ET.fromstring(xml_bytes)
        texts = []
        for el in root.iter():
            if el.text and el.text.strip():
                texts.append(html.unescape(el.text.strip()))
        return " ".join(texts)
    except ET.ParseError:
        raw = xml_bytes.decode("utf-8", errors="replace")
        return re.sub(r"<[^>]+>", " ", raw)


def _parse_rss_items(data: bytes, source_name: str) -> List[Dict[str, str]]:
    results = []
    try:
        root = ET.fromstring(data)
        # RSS 2.0
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            desc_raw = (item.findtext("description") or "").strip()
            desc = re.sub(r"<[^>]+>", " ", html.unescape(desc_raw)).strip()
            pub = (item.findtext("pubDate") or "").strip()
            raw = f"{title}\n{desc}".strip()
            date_str = _parse_rfc822_date(pub) if pub else _NOW_DATE
            if raw:
                results.append({
                    "source": source_name,
                    "raw_text": raw[:MAX_TEXT_LEN],
                    "published_at": date_str,
                    "collected_at": _NOW_DATE,
                })
        if not results:
            # Atom
            for entry in root.findall(".//{http://www.w3.org/2005/Atom}entry"):
                title_el = entry.find("{http://www.w3.org/2005/Atom}title")
                summary_el = entry.find("{http://www.w3.org/2005/Atom}summary")
                content_el = entry.find("{http://www.w3.org/2005/Atom}content")
                updated_el = entry.find("{http://www.w3.org/2005/Atom}updated")
                title = (title_el.text if title_el is not None else "") or ""
                body = ""
                if content_el is not None and content_el.text:
                    body = re.sub(r"<[^>]+>", " ", html.unescape(content_el.text)).strip()
                elif summary_el is not None and summary_el.text:
                    body = re.sub(r"<[^>]+>", " ", html.unescape(summary_el.text)).strip()
                pub = (updated_el.text if updated_el is not None else "") or ""
                date_str = pub[:10] if len(pub) >= 10 else _NOW_DATE
                raw = f"{title.strip()}\n{body}".strip()
                if raw:
                    results.append({
                        "source": source_name,
                        "raw_text": raw[:MAX_TEXT_LEN],
                        "published_at": date_str,
                        "collected_at": _NOW_DATE,
                    })
    except ET.ParseError as exc:
        logger.error("RSS parse error for %s: %s", source_name, exc)
    return results


def _parse_rfc822_date(s: str) -> str:
    """Try to parse RFC-822 date; fall back to today."""
    for fmt in (
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
        "%d %b %Y %H:%M:%S %z",
    ):
        try:
            return datetime.strptime(s.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return _NOW_DATE


# ── YouTube ──────────────────────────────────────────────
#  策略：GET watch page HTML → 抓 ytInitialPlayerResponse JSON
#        → captions → captionTracks → baseUrl → GET XML → strip tags

_YT_PLAYER_RE = re.compile(
    r"ytInitialPlayerResponse\s*=\s*(\{.+?\})\s*;", re.DOTALL
)


async def _scrape_youtube_video(video_id: str, source_name: str) -> Optional[Dict[str, str]]:
    watch_url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        html_bytes = await _fetch(
            watch_url,
            extra_headers={"Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8"},
        )
    except Exception as exc:
        logger.warning("YouTube fetch failed for %s: %s", video_id, exc)
        return None

    html_text = html_bytes.decode("utf-8", errors="replace")
    m = _YT_PLAYER_RE.search(html_text)
    if not m:
        logger.warning("ytInitialPlayerResponse not found for video %s", video_id)
        return None

    try:
        player_json = json.loads(m.group(1))
    except json.JSONDecodeError as exc:
        logger.warning("ytInitialPlayerResponse JSON parse failed for %s: %s", video_id, exc)
        return None

    # Extract title from videoDetails
    video_details = player_json.get("videoDetails", {})
    title = video_details.get("title", "")
    publish_date = video_details.get("publishDate", _NOW_DATE)
    date_str = publish_date[:10] if publish_date else _NOW_DATE

    # Try to get captions
    caption_text = ""
    try:
        tracks = (
            player_json
            .get("captions", {})
            .get("playerCaptionsTracklistRenderer", {})
            .get("captionTracks", [])
        )
        if tracks:
            base_url = tracks[0].get("baseUrl", "")
            if base_url:
                cap_bytes = await _fetch(base_url)
                caption_text = _strip_xml_tags(cap_bytes)
    except Exception as exc:
        logger.warning("Caption fetch failed for %s: %s", video_id, exc)

    raw = f"{title}\n{caption_text}".strip()
    if not raw:
        return None
    return {
        "source": source_name,
        "raw_text": raw[:MAX_TEXT_LEN],
        "published_at": date_str,
        "collected_at": _NOW_DATE,
    }


async def _scrape_youtube(source: Dict[str, Any]) -> List[Dict[str, str]]:
    name = source["name"]
    video_ids = source.get("ids", [])
    tasks = [_scrape_youtube_video(vid, name) for vid in video_ids]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)
    results = []
    for r in raw_results:
        if isinstance(r, Exception):
            logger.error("YouTube video error: %s", r)
        elif r is not None:
            results.append(r)
    return results


# ── Bilibili ──────────────────────────────────────────────
#  GET api.bilibili.com/x/polymer/web-dynamic/v1/feed/space?host_mid={uid}
#  取 items -> modules -> module_dynamic -> desc -> text

async def _scrape_bilibili_uid(uid: str, source_name: str) -> List[Dict[str, str]]:
    url = (
        f"https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space"
        f"?host_mid={uid}&platform=web"
    )
    try:
        data = await _fetch(
            url,
            extra_headers={
                "Referer": f"https://space.bilibili.com/{uid}/dynamic",
                "Origin": "https://space.bilibili.com",
            },
        )
    except Exception as exc:
        logger.warning("Bilibili fetch failed uid=%s: %s", uid, exc)
        return []

    results = []
    try:
        obj = json.loads(data.decode("utf-8"))
        items = obj.get("data", {}).get("items", [])
        for item in items:
            modules = item.get("modules", {})
            dyn = modules.get("module_dynamic", {})
            desc = dyn.get("desc", {})
            text = (desc.get("text") or "").strip() if desc else ""
            # Also grab title from major if available
            major = dyn.get("major", {})
            archive = major.get("archive", {}) if major else {}
            title = (archive.get("title") or "").strip()
            pub_ts = modules.get("module_author", {}).get("pub_ts", 0)
            date_str = (
                datetime.fromtimestamp(pub_ts, tz=timezone.utc).strftime("%Y-%m-%d")
                if pub_ts else _NOW_DATE
            )
            raw = f"{title}\n{text}".strip()
            if raw:
                results.append({
                    "source": source_name,
                    "raw_text": raw[:MAX_TEXT_LEN],
                    "published_at": date_str,
                    "collected_at": _NOW_DATE,
                })
    except (json.JSONDecodeError, AttributeError, KeyError) as exc:
        logger.error("Bilibili parse error uid=%s: %s", uid, exc)
    return results


async def _scrape_bilibili(source: Dict[str, Any]) -> List[Dict[str, str]]:
    name = source["name"]
    results = []
    for uid in source.get("ids", []):
        results.extend(await _scrape_bilibili_uid(uid, name))
    return results


# ── Twitter / X via Nitter RSS ────────────────────────────

async def _scrape_twitter_uid(uid: str, source_name: str,
                               nodes: List[str]) -> List[Dict[str, str]]:
    urls = [f"{node.rstrip('/')}/{uid}/rss" for node in nodes]
    data = await _fetch_failover(urls)
    if data is None:
        logger.warning("All Nitter nodes failed for %s", uid)
        return []
    return _parse_rss_items(data, source_name)


async def _scrape_twitter(source: Dict[str, Any]) -> List[Dict[str, str]]:
    name = source["name"]
    nodes = source.get("nodes", ["https://nitter.net"])
    results = []
    for uid in source.get("ids", []):
        results.extend(await _scrape_twitter_uid(uid, name, nodes))
    return results


# ── RSSHub-based: Douyin / TikTok / Xiaohongshu ──────────

_RSSHUB_ROUTES = {
    "douyin":      "/douyin/user/{uid}",
    "tiktok":      "/tiktok/user/@{uid}",
    "xiaohongshu": "/xiaohongshu/user/{uid}",
}


async def _scrape_rsshub_uid(uid: str, source_name: str, stype: str,
                              nodes: List[str]) -> List[Dict[str, str]]:
    route_tpl = _RSSHUB_ROUTES[stype]
    route = route_tpl.format(uid=uid)
    urls = [f"{node.rstrip('/')}{route}" for node in nodes]
    data = await _fetch_failover(urls)
    if data is None:
        logger.warning("All RSSHub nodes failed for %s uid=%s", stype, uid)
        return []
    return _parse_rss_items(data, source_name)


async def _scrape_rsshub_source(source: Dict[str, Any]) -> List[Dict[str, str]]:
    name = source["name"]
    stype = source["type"]
    nodes = source.get("nodes", ["https://rsshub.app"])
    results = []
    for uid in source.get("ids", []):
        results.extend(await _scrape_rsshub_uid(uid, name, stype, nodes))
    return results


# ── 頂層排程 ──────────────────────────────────────────────

async def _scrape_source(source: Dict[str, Any]) -> List[Dict[str, str]]:
    stype = source.get("type", "")
    if stype == "youtube":
        return await _scrape_youtube(source)
    elif stype == "bilibili":
        return await _scrape_bilibili(source)
    elif stype == "twitter":
        return await _scrape_twitter(source)
    elif stype in ("douyin", "tiktok", "xiaohongshu"):
        return await _scrape_rsshub_source(source)
    else:
        logger.warning("Unknown source type '%s', skipping.", stype)
        return []


async def _scrape_all_impl() -> List[Dict[str, str]]:
    if not SOURCES:
        logger.info("SOURCES is empty, returning [].")
        return []
    tasks = [_scrape_source(src) for src in SOURCES]
    nested = await asyncio.gather(*tasks, return_exceptions=True)
    results: List[Dict[str, str]] = []
    for r in nested:
        if isinstance(r, Exception):
            logger.error("Source scrape error: %s", r)
        elif isinstance(r, list):
            results.extend(r)
    return results


async def scrape_all() -> List[Dict[str, str]]:
    try:
        return await asyncio.wait_for(_scrape_all_impl(), timeout=GLOBAL_TIMEOUT)
    except asyncio.TimeoutError:
        logger.error("Global scrape timeout (%s s) exceeded.", GLOBAL_TIMEOUT)
        return []
