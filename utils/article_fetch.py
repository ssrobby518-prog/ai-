"""Full-text article fetcher with retry, quality gate, and async support.

v0.2.3: Added error classification, retry with exponential backoff + jitter,
multi-strategy extraction (trafilatura + BeautifulSoup fallback), quality
gate, and async enrichment via asyncio + aiohttp.

All settings have env-var overrides with sensible defaults.
"""

from __future__ import annotations

import asyncio
import os
import random
import re
import time
from urllib.parse import urlparse

import requests
import trafilatura
from bs4 import BeautifulSoup
from schemas.models import RawItem

from utils.logger import get_logger
from utils.metrics import EnrichStats

# ---------------------------------------------------------------------------
# Configuration (env-var overridable)
# ---------------------------------------------------------------------------

_FETCH_TIMEOUT = int(os.getenv("ENRICH_FETCH_TIMEOUT", "15"))
_POLITENESS_DELAY = float(os.getenv("ENRICH_POLITENESS_DELAY", "0.5"))
_MAX_RETRIES = int(os.getenv("ENRICH_MAX_RETRIES", "2"))
_SEMAPHORE_LIMIT = int(os.getenv("ENRICH_CONCURRENCY", "3"))
_MIN_TEXT_LENGTH = int(os.getenv("ENRICH_MIN_TEXT_LENGTH", "400"))
_JUNK_RATIO_MAX = float(os.getenv("ENRICH_JUNK_RATIO_MAX", "0.3"))

# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------

ERR_TIMEOUT = "timeout"
ERR_HTTP_ERROR = "http_error"
ERR_BLOCKED = "blocked"
ERR_EXTRACT_EMPTY = "extract_empty"
ERR_EXTRACT_LOW_QUALITY = "extract_low_quality"
ERR_SKIPPED_POLICY = "skipped_policy"
ERR_CONNECTION = "connection_error"

# HTTP codes that indicate blocking / rate-limiting
_BLOCKED_CODES = {401, 403, 429, 451}


# ---------------------------------------------------------------------------
# Detection: does this item need full-text enrichment?
# ---------------------------------------------------------------------------


def _needs_fulltext(item: RawItem) -> bool:
    """Return True if the item body is metadata-only and needs enrichment."""
    body = item.body

    # Pattern 1: hnrss.org metadata (contains "Comments URL:" + ycombinator link)
    if "Comments URL:" in body and "ycombinator.com" in body:
        return True

    # Pattern 2: Algolia fallback — body equals title (no story_text available)
    if body.strip() == item.title.strip() and body.strip():
        return True

    # Pattern 3: HN source with very short body (likely just metadata)
    return item.source_name.lower() in ("hackernews", "hn", "hacker news") and len(body) < 200


# ---------------------------------------------------------------------------
# Quality gate
# ---------------------------------------------------------------------------


def _junk_char_ratio(text: str) -> float:
    """Ratio of non-alphanumeric, non-CJK, non-space characters."""
    if not text:
        return 1.0
    junk = sum(1 for c in text if not (c.isalnum() or c.isspace() or "\u4e00" <= c <= "\u9fff"))
    return junk / len(text)


def _check_quality(text: str) -> str | None:
    """Return an error code if text fails quality gate, else None."""
    if not text:
        return ERR_EXTRACT_EMPTY
    if len(text) < _MIN_TEXT_LENGTH:
        return ERR_EXTRACT_LOW_QUALITY
    if _junk_char_ratio(text) > _JUNK_RATIO_MAX:
        return ERR_EXTRACT_LOW_QUALITY
    return None


# ---------------------------------------------------------------------------
# Multi-strategy extraction
# ---------------------------------------------------------------------------


def _extract_with_trafilatura(html: str) -> str:
    """Primary extraction via trafilatura."""
    try:
        return (trafilatura.extract(html) or "").strip()
    except Exception:
        return ""


def _extract_with_bs4(html: str) -> str:
    """Fallback extraction via BeautifulSoup (already in deps)."""
    try:
        soup = BeautifulSoup(html, "html.parser")
        # Remove script/style
        for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        # Collapse whitespace
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
    except Exception:
        return ""


def _extract_text(html: str) -> str:
    """Try trafilatura first, then BS4 fallback."""
    text = _extract_with_trafilatura(html)
    if text and len(text) >= _MIN_TEXT_LENGTH:
        return text
    fallback = _extract_with_bs4(html)
    if fallback and len(fallback) > len(text):
        return fallback
    return text  # return whatever trafilatura got (even if short)


# ---------------------------------------------------------------------------
# Synchronous fetch with retry
# ---------------------------------------------------------------------------


def fetch_article_text(url: str) -> tuple[str, str]:
    """Fetch URL and extract article text with retry.

    Returns ``(text, error_code)``. On success ``error_code`` is ``""``.
    """
    log = get_logger()
    last_error = ERR_EXTRACT_EMPTY

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = requests.get(
                url,
                timeout=_FETCH_TIMEOUT,
                headers={"User-Agent": "AI-Intel-Scraper/1.0"},
            )
            if resp.status_code in _BLOCKED_CODES:
                return "", ERR_BLOCKED
            resp.raise_for_status()
        except requests.Timeout:
            last_error = ERR_TIMEOUT
            log.debug("Timeout fetching %s (attempt %d/%d)", url, attempt, _MAX_RETRIES)
            if attempt < _MAX_RETRIES:
                time.sleep(1.0 * attempt + random.uniform(0, 0.5))
            continue
        except requests.ConnectionError:
            last_error = ERR_CONNECTION
            if attempt < _MAX_RETRIES:
                time.sleep(1.0 * attempt + random.uniform(0, 0.5))
            continue
        except requests.HTTPError:
            return "", ERR_HTTP_ERROR
        except Exception as exc:
            log.debug("Unexpected error fetching %s: %s", url, exc)
            return "", ERR_HTTP_ERROR

        # Extraction
        text = _extract_text(resp.text)
        quality_err = _check_quality(text)
        if quality_err is None:
            return text, ""
        last_error = quality_err
        # Don't retry extraction failures — HTML won't change
        break

    return "", last_error


# ---------------------------------------------------------------------------
# Synchronous enrich (backward compatible)
# ---------------------------------------------------------------------------


def enrich_items(items: list[RawItem], stats: EnrichStats | None = None) -> list[RawItem]:
    """Enrich items that have metadata-only bodies with full article text.

    Accepts an optional ``EnrichStats`` to record metrics.
    """
    log = get_logger()
    if stats is None:
        stats = EnrichStats()
    enriched_count = 0

    for item in items:
        if not _needs_fulltext(item):
            continue

        t0 = time.time()
        text, err = fetch_article_text(item.url)
        latency = time.time() - t0

        if err:
            stats.record_fail(err, latency)
            # On low_quality, keep the extracted text if it's better than current
            if err == ERR_EXTRACT_LOW_QUALITY and text and len(text) > len(item.body):
                item.body = text
                enriched_count += 1
        else:
            stats.record_success(latency)
            if text and len(text) > len(item.body):
                item.body = text
                enriched_count += 1

        time.sleep(_POLITENESS_DELAY)

    if enriched_count:
        log.info("Enriched %d/%d items with full article text", enriched_count, len(items))

    return items


# ---------------------------------------------------------------------------
# Async enrich
# ---------------------------------------------------------------------------


async def _async_fetch_one(
    url: str,
    semaphore: asyncio.Semaphore,
    domain_locks: dict[str, float],
    domain_lock: asyncio.Lock,
) -> tuple[str, str, float]:
    """Fetch and extract one URL with concurrency + per-domain politeness.

    Returns ``(text, error_code, latency)``.
    """
    import aiohttp

    domain = urlparse(url).netloc
    t0 = time.time()

    async with semaphore:
        # Per-domain politeness
        async with domain_lock:
            last_req = domain_locks.get(domain, 0.0)
            wait = _POLITENESS_DELAY - (time.time() - last_req)
            if wait > 0:
                await asyncio.sleep(wait)
            domain_locks[domain] = time.time()

        last_error = ERR_EXTRACT_EMPTY

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                async with (
                    aiohttp.ClientSession() as session,
                    session.get(
                        url,
                        timeout=aiohttp.ClientTimeout(total=_FETCH_TIMEOUT),
                        headers={"User-Agent": "AI-Intel-Scraper/1.0"},
                    ) as resp,
                ):
                    if resp.status in _BLOCKED_CODES:
                        return "", ERR_BLOCKED, time.time() - t0
                    resp.raise_for_status()
                    html = await resp.text()
            except TimeoutError:
                last_error = ERR_TIMEOUT
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(1.0 * attempt + random.uniform(0, 0.5))
                continue
            except Exception:
                last_error = ERR_CONNECTION
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(1.0 * attempt + random.uniform(0, 0.5))
                continue

            text = _extract_text(html)
            quality_err = _check_quality(text)
            if quality_err is None:
                return text, "", time.time() - t0
            last_error = quality_err
            break

        return "", last_error, time.time() - t0


async def _enrich_items_async_impl(
    items: list[RawItem],
    stats: EnrichStats,
) -> list[RawItem]:
    """Async implementation of item enrichment."""
    log = get_logger()
    semaphore = asyncio.Semaphore(_SEMAPHORE_LIMIT)
    domain_locks: dict[str, float] = {}
    domain_lock = asyncio.Lock()

    to_enrich = [(i, item) for i, item in enumerate(items) if _needs_fulltext(item)]
    if not to_enrich:
        return items

    async def _process(idx: int, item: RawItem) -> None:
        text, err, latency = await _async_fetch_one(
            item.url,
            semaphore,
            domain_locks,
            domain_lock,
        )
        if err:
            stats.record_fail(err, latency)
            if err == ERR_EXTRACT_LOW_QUALITY and text and len(text) > len(item.body):
                item.body = text
        else:
            stats.record_success(latency)
            if text and len(text) > len(item.body):
                item.body = text

    tasks = [_process(idx, item) for idx, item in to_enrich]
    await asyncio.gather(*tasks)

    enriched = stats.success
    if enriched:
        log.info("Async enriched %d/%d items with full article text", enriched, len(items))
    return items


def enrich_items_async(items: list[RawItem], stats: EnrichStats | None = None) -> list[RawItem]:
    """Async enrichment entry point (sync wrapper).

    Falls back to synchronous ``enrich_items`` if the event loop cannot be
    created (e.g. already running inside an async context).
    """
    if stats is None:
        stats = EnrichStats()

    try:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_enrich_items_async_impl(items, stats))
        finally:
            loop.close()
    except RuntimeError:
        # Event loop already running — fall back to sync
        return enrich_items(items, stats)
