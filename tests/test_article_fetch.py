"""Tests for utils.article_fetch — all network calls mocked."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import requests
from schemas.models import RawItem
from utils.article_fetch import _needs_fulltext, enrich_items, fetch_article_text

# ---------------------------------------------------------------------------
# _needs_fulltext
# ---------------------------------------------------------------------------


def _make_item(**kwargs) -> RawItem:
    defaults = {
        "item_id": "abc123",
        "title": "Test Title",
        "url": "https://example.com/article",
        "body": "Some body text that is long enough to not trigger short-body heuristic. " * 5,
        "published_at": "2026-01-01T00:00:00+00:00",
        "source_name": "TechCrunch",
        "source_category": "startup",
        "lang": "en",
    }
    defaults.update(kwargs)
    return RawItem(**defaults)


class TestNeedsFulltext:
    def test_hn_rss_metadata(self):
        """hnrss.org items with Comments URL + ycombinator.com → needs fulltext."""
        item = _make_item(
            source_name="HN",
            body=(
                "Article URL: https://example.com/article\n"
                "Comments URL: https://news.ycombinator.com/item?id=12345\n"
                "Points: 150"
            ),
        )
        assert _needs_fulltext(item) is True

    def test_title_equals_body(self):
        """Algolia fallback where body == title → needs fulltext."""
        item = _make_item(
            source_name="HackerNews",
            title="Show HN: My Cool Project",
            body="Show HN: My Cool Project",
        )
        assert _needs_fulltext(item) is True

    def test_hn_short_body(self):
        """HackerNews source with very short body → needs fulltext."""
        item = _make_item(
            source_name="HackerNews",
            body="Short stub",
        )
        assert _needs_fulltext(item) is True

    def test_normal_body(self):
        """Item with substantial body text → does NOT need fulltext."""
        item = _make_item(
            source_name="TechCrunch",
            body="A " * 300,  # 600 chars, clearly real content
        )
        assert _needs_fulltext(item) is False

    def test_non_hn_short_body(self):
        """Non-HN source with short body → does NOT trigger (only HN triggers)."""
        item = _make_item(
            source_name="TechCrunch",
            body="Short stub",
        )
        assert _needs_fulltext(item) is False


# ---------------------------------------------------------------------------
# fetch_article_text  (v0.2.3 — returns (text, error_code) tuple)
# ---------------------------------------------------------------------------

# A string long enough to pass the quality gate (_MIN_TEXT_LENGTH = 400)
_GOOD_TEXT = "Article content extracted from the web page. " * 15  # ~690 chars


class TestFetchArticleText:
    @patch("utils.article_fetch.requests.get")
    @patch("utils.article_fetch._extract_text")
    def test_success(self, mock_extract, mock_get):
        """Successful fetch + extract returns (text, '')."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><body><p>Article content here.</p></body></html>"
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp
        mock_extract.return_value = _GOOD_TEXT

        text, err = fetch_article_text("https://example.com/article")
        assert text == _GOOD_TEXT
        assert err == ""
        mock_get.assert_called_once()

    @patch("utils.article_fetch.requests.get")
    def test_timeout(self, mock_get):
        """Timeout returns ('', 'timeout') after retries."""
        mock_get.side_effect = requests.Timeout("Connection timed out")

        with patch("utils.article_fetch.time.sleep"):  # skip retry delay
            text, err = fetch_article_text("https://example.com/article")
        assert text == ""
        assert err == "timeout"

    @patch("utils.article_fetch.requests.get")
    @patch("utils.article_fetch._extract_text")
    def test_extract_returns_empty(self, mock_extract, mock_get):
        """Extraction returning empty string → ('', 'extract_empty')."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html></html>"
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp
        mock_extract.return_value = ""

        text, err = fetch_article_text("https://example.com/article")
        assert text == ""
        assert err == "extract_empty"

    @patch("utils.article_fetch.requests.get")
    def test_blocked(self, mock_get):
        """HTTP 403 returns ('', 'blocked')."""
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_get.return_value = mock_resp

        text, err = fetch_article_text("https://example.com/article")
        assert text == ""
        assert err == "blocked"


# ---------------------------------------------------------------------------
# enrich_items  (v0.2.3 — fetch_article_text returns tuple)
# ---------------------------------------------------------------------------


class TestEnrichItems:
    @patch("utils.article_fetch.time.sleep")  # skip politeness delay in tests
    @patch("utils.article_fetch.fetch_article_text")
    def test_mixed_items(self, mock_fetch, mock_sleep):
        """Only HN items with metadata-only bodies get enriched."""
        hn_item = _make_item(
            source_name="HackerNews",
            title="Cool HN Post",
            body="Cool HN Post",  # title == body → needs fulltext
            url="https://example.com/hn-article",
        )
        tc_item = _make_item(
            source_name="TechCrunch",
            body="Real TechCrunch article content that is long enough. " * 10,
            url="https://techcrunch.com/article",
        )

        full_text = "Full article text extracted from the web page. " * 10
        mock_fetch.return_value = (full_text, "")

        result = enrich_items([hn_item, tc_item])

        assert len(result) == 2
        # HN item should be enriched
        assert "Full article text" in result[0].body
        # TC item should be unchanged
        assert "Real TechCrunch article content" in result[1].body
        # fetch_article_text called only for HN item
        mock_fetch.assert_called_once_with("https://example.com/hn-article")

    @patch("utils.article_fetch.time.sleep")
    @patch("utils.article_fetch.fetch_article_text")
    def test_no_enrichment_when_fetch_fails(self, mock_fetch, mock_sleep):
        """If fetch returns error, original body is kept."""
        item = _make_item(
            source_name="HackerNews",
            title="Some Post",
            body="Some Post",
        )
        original_body = item.body
        mock_fetch.return_value = ("", "timeout")

        result = enrich_items([item])
        assert result[0].body == original_body

    @patch("utils.article_fetch.time.sleep")
    @patch("utils.article_fetch.fetch_article_text")
    def test_no_enrichment_when_fetched_text_shorter(self, mock_fetch, mock_sleep):
        """If fetched text is shorter than existing body, keep original."""
        item = _make_item(
            source_name="HackerNews",
            body=(
                "Article URL: https://example.com\n"
                "Comments URL: https://news.ycombinator.com/item?id=123\n"
                "Points: 50\nExtra metadata lines here for padding to make body longer"
            ),
        )
        original_body = item.body
        mock_fetch.return_value = ("Short", "extract_low_quality")

        result = enrich_items([item])
        assert result[0].body == original_body
