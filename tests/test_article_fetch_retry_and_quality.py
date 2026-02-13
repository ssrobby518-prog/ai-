"""Tests for article_fetch retry strategy, quality gate, and fallback extractor."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import requests
from schemas.models import RawItem
from utils.article_fetch import (
    ERR_BLOCKED,
    ERR_EXTRACT_EMPTY,
    ERR_EXTRACT_LOW_QUALITY,
    _check_quality,
    enrich_items,
    fetch_article_text,
)
from utils.metrics import EnrichStats


def _make_item(**kwargs) -> RawItem:
    defaults = {
        "item_id": "abc123",
        "title": "Test Title",
        "url": "https://example.com/article",
        "body": "Test Title",  # body == title → needs fulltext
        "published_at": "2026-01-01T00:00:00+00:00",
        "source_name": "HackerNews",
        "source_category": "tech",
        "lang": "en",
    }
    defaults.update(kwargs)
    return RawItem(**defaults)


class TestRetryStrategy:
    @patch("utils.article_fetch.requests.get")
    @patch("utils.article_fetch.time.sleep")
    def test_timeout_then_success(self, mock_sleep, mock_get):
        """First attempt times out, second succeeds → enrich_success."""
        timeout_exc = requests.Timeout("Connection timed out")
        success_resp = MagicMock()
        success_resp.status_code = 200
        success_resp.raise_for_status = MagicMock()
        success_resp.text = "<html><body>" + "<p>Real article content. </p>" * 50 + "</body></html>"

        mock_get.side_effect = [timeout_exc, success_resp]

        with patch("utils.article_fetch._extract_text", return_value="Real article content. " * 50):
            text, err = fetch_article_text("https://example.com/article")

        assert err == ""
        assert "Real article content" in text
        assert mock_get.call_count == 2

    @patch("utils.article_fetch.requests.get")
    def test_403_returns_blocked(self, mock_get):
        """403 response → blocked error code, no retry."""
        resp = MagicMock()
        resp.status_code = 403
        mock_get.return_value = resp

        _text, err = fetch_article_text("https://example.com/article")
        assert err == ERR_BLOCKED
        assert _text == ""
        assert mock_get.call_count == 1

    @patch("utils.article_fetch.requests.get")
    def test_429_returns_blocked(self, mock_get):
        """429 response → blocked error code."""
        resp = MagicMock()
        resp.status_code = 429
        mock_get.return_value = resp

        _text, err = fetch_article_text("https://example.com/article")
        assert err == ERR_BLOCKED


class TestFallbackExtractor:
    @patch("utils.article_fetch.requests.get")
    def test_trafilatura_empty_bs4_success(self, mock_get):
        """trafilatura returns empty → BS4 fallback extracts text."""
        html = "<html><body>" + "<p>Paragraph of real content here. </p>" * 40 + "</body></html>"
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.text = html

        mock_get.return_value = resp

        with patch("utils.article_fetch._extract_with_trafilatura", return_value=""):
            text, err = fetch_article_text("https://example.com/article")

        # BS4 should have extracted the text
        assert "Paragraph of real content here" in text or err == ERR_EXTRACT_LOW_QUALITY


class TestQualityGate:
    def test_too_short(self):
        assert _check_quality("Short text") == ERR_EXTRACT_LOW_QUALITY

    def test_empty(self):
        assert _check_quality("") == ERR_EXTRACT_EMPTY

    def test_passes(self):
        assert _check_quality("A" * 500) is None

    @patch("utils.article_fetch.time.sleep")
    @patch("utils.article_fetch.fetch_article_text")
    def test_low_quality_keeps_original_body(self, mock_fetch, mock_sleep):
        """Low-quality extraction preserves original body in stats."""
        item = _make_item()
        original_body = item.body
        stats = EnrichStats()

        # Return short text → low_quality
        mock_fetch.return_value = ("Short", ERR_EXTRACT_LOW_QUALITY)

        result = enrich_items([item], stats=stats)
        # Original body is kept because "Short" is not longer than "Test Title"
        assert result[0].body == original_body
        assert stats.fail == 1
        assert ERR_EXTRACT_LOW_QUALITY in stats.fail_reasons


class TestStatsTracking:
    @patch("utils.article_fetch.time.sleep")
    @patch("utils.article_fetch.fetch_article_text")
    def test_blocked_counted_correctly(self, mock_fetch, mock_sleep):
        """403/429 blocked errors are counted in stats."""
        items = [_make_item(item_id=f"item_{i}", url=f"https://ex.com/{i}") for i in range(3)]
        stats = EnrichStats()

        mock_fetch.side_effect = [
            ("Full text " * 100, ""),
            ("", ERR_BLOCKED),
            ("Full text " * 100, ""),
        ]

        enrich_items(items, stats=stats)
        assert stats.success == 2
        assert stats.fail == 1
        assert stats.fail_reasons.get(ERR_BLOCKED, 0) == 1
