"""Tests for the sources plugin architecture."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.sources.base import NewsSource
from schemas.models import RawItem


def test_base_class_not_instantiable() -> None:
    """NewsSource ABC cannot be instantiated directly."""
    with pytest.raises(TypeError):
        NewsSource()  # type: ignore[abstract]


def test_discover_sources_finds_builtin_plugins() -> None:
    """discover_sources should find built-in RSS and mock social plugins."""
    from core.sources import discover_sources

    sources = discover_sources()
    names = [s.name for s in sources]
    assert "HackerNews" in names
    assert "TechCrunch" in names
    assert "36kr" in names
    assert "Reddit AI" in names
    assert "Bilibili Tech" in names
    assert "Xiaohongshu AI" in names
    assert "CSDN" in names
    assert "Dcard Tech" in names
    assert "Instagram AI" in names


def test_discover_sources_returns_newsource_instances() -> None:
    from core.sources import discover_sources

    for src in discover_sources():
        assert isinstance(src, NewsSource)


def test_fetch_all_sources_returns_rawitem_list() -> None:
    """fetch_all_sources should return a list (possibly empty) of RawItem."""
    with (
        patch("core.news_sources.fetch_hackernews", return_value=[]),
        patch("core.sources.techcrunch_rss.fetch_feed", return_value=[]),
        patch("core.sources.kr36.fetch_feed", return_value=[]),
    ):
        from core.sources import fetch_all_sources

        result = fetch_all_sources()
        assert isinstance(result, list)


def test_hackernews_plugin_fetch() -> None:
    """HackerNews plugin should delegate to fetch_hackernews."""
    fake_item = RawItem(
        item_id="abc",
        title="Test",
        url="https://example.com",
        body="body",
        published_at="2025-01-01",
        source_name="HackerNews",
        source_category="tech",
        lang="en",
    )
    with patch("core.news_sources.fetch_hackernews", return_value=[fake_item]):
        from core.sources.hackernews import HackerNewsSource

        src = HackerNewsSource()
        items = src.fetch()
        assert len(items) == 1
        assert items[0].source_name == "HackerNews"


def test_hackernews_plugin_handles_failure() -> None:
    """HackerNews plugin should return empty list on failure."""
    with patch("core.news_sources.fetch_hackernews", side_effect=Exception("boom")):
        from core.sources.hackernews import HackerNewsSource

        src = HackerNewsSource()
        items = src.fetch()
        assert items == []


def test_techcrunch_plugin_delegates_to_fetch_feed() -> None:
    """TechCrunch plugin should call fetch_feed with the right config."""
    with patch("core.sources.techcrunch_rss.fetch_feed", return_value=[]) as mock_ff:
        from core.sources.techcrunch_rss import TechCrunchSource

        src = TechCrunchSource()
        result = src.fetch()
        assert result == []
        mock_ff.assert_called_once()
        call_cfg = mock_ff.call_args[0][0]
        assert call_cfg["name"] == "TechCrunch"


def test_kr36_plugin_delegates_to_fetch_feed() -> None:
    """36kr plugin should call fetch_feed with the right config."""
    with patch("core.sources.kr36.fetch_feed", return_value=[]) as mock_ff:
        from core.sources.kr36 import Kr36Source

        src = Kr36Source()
        result = src.fetch()
        assert result == []
        mock_ff.assert_called_once()
        call_cfg = mock_ff.call_args[0][0]
        assert call_cfg["name"] == "36kr"


@pytest.mark.parametrize(
    ("module_path", "class_name"),
    [
        ("core.sources.reddit_ai", "RedditAISource"),
        ("core.sources.bilibili_tech", "BilibiliTechSource"),
        ("core.sources.xiaohongshu_ai", "XiaohongshuAISource"),
        ("core.sources.csdn", "CSDNSource"),
        ("core.sources.dcard_tech", "DcardTechSource"),
        ("core.sources.instagram_ai", "InstagramAISource"),
    ],
)
def test_new_source_plugins_are_importable(module_path: str, class_name: str) -> None:
    module = __import__(module_path, fromlist=[class_name])
    cls = getattr(module, class_name)
    src = cls()
    items = src.fetch()

    assert isinstance(items, list)
    assert items, f"{class_name} should return mock RawItem payloads"
    assert all(isinstance(item, RawItem) for item in items)
