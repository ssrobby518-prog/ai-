"""Tests for core/z0_loader.py — no network, uses tmp_path fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.z0_loader import _z0_to_raw_item, load_z0_items
from schemas.models import RawItem


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_z0_record(
    title: str = "GPT-5 released by OpenAI with benchmark improvements",
    url: str = "https://openai.com/blog/gpt5-release",
    platform: str = "openai",
    tag: str = "official",
    feed_name: str = "OpenAI News",
    published_at: str = "2026-02-19T09:00:00+00:00",
    summary: str = "OpenAI launched GPT-5 with 40% improvement in benchmark scores.",
    frontier_score: int = 92,
    content_text: str = "",
    z0_id: str = "abc123def456abcd",
) -> dict:
    return {
        "id": z0_id,
        "title": title,
        "url": url,
        "domain": url.split("/")[2] if url.startswith("http") and len(url.split("/")) > 2 else "",
        "published_at": published_at,
        "summary": summary,
        "content_text": content_text,
        "frontier_score": frontier_score,
        "source": {
            "platform": platform,
            "feed_name": feed_name,
            "feed_url": f"https://{platform}.com/rss",
            "tag": tag,
        },
        "collected_at": "2026-02-19T10:00:00+00:00",
    }


def _write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# _z0_to_raw_item unit tests
# ---------------------------------------------------------------------------

class TestZ0ToRawItem:
    def test_basic_mapping(self):
        rec = _make_z0_record()
        item = _z0_to_raw_item(rec)
        assert item is not None
        assert isinstance(item, RawItem)

    def test_title_preserved(self):
        rec = _make_z0_record(title="NVIDIA H200 GPU released at SC24")
        item = _z0_to_raw_item(rec)
        assert item is not None
        assert item.title == "NVIDIA H200 GPU released at SC24"

    def test_url_preserved(self):
        rec = _make_z0_record(url="https://example.com/article-123")
        item = _z0_to_raw_item(rec)
        assert item is not None
        assert item.url == "https://example.com/article-123"

    def test_body_uses_content_text_when_present(self):
        rec = _make_z0_record(content_text="Full article text here.", summary="Short summary.")
        item = _z0_to_raw_item(rec)
        assert item is not None
        assert item.body == "Full article text here."

    def test_body_falls_back_to_summary(self):
        rec = _make_z0_record(content_text="", summary="Short summary fallback.")
        item = _z0_to_raw_item(rec)
        assert item is not None
        assert item.body == "Short summary fallback."

    def test_published_at_preserved(self):
        rec = _make_z0_record(published_at="2026-02-19T09:00:00+00:00")
        item = _z0_to_raw_item(rec)
        assert item is not None
        assert item.published_at == "2026-02-19T09:00:00+00:00"

    def test_source_name_from_feed_name(self):
        rec = _make_z0_record(feed_name="OpenAI News")
        item = _z0_to_raw_item(rec)
        assert item is not None
        assert item.source_name == "OpenAI News"

    def test_source_name_falls_back_to_platform(self):
        rec = _make_z0_record(feed_name="", platform="anthropic")
        rec["source"]["feed_name"] = ""
        item = _z0_to_raw_item(rec)
        assert item is not None
        assert item.source_name == "anthropic"

    def test_lang_en_for_english_platform(self):
        rec = _make_z0_record(platform="openai")
        item = _z0_to_raw_item(rec)
        assert item is not None
        assert item.lang == "en"

    def test_lang_zh_for_chinese_platform(self):
        rec = _make_z0_record(platform="36kr", tag="zh_media")
        item = _z0_to_raw_item(rec)
        assert item is not None
        assert item.lang == "zh"

    def test_frontier_score_attached(self):
        rec = _make_z0_record(frontier_score=88)
        item = _z0_to_raw_item(rec)
        assert item is not None
        assert getattr(item, "z0_frontier_score", None) == 88

    def test_platform_attached(self):
        rec = _make_z0_record(platform="huggingface")
        item = _z0_to_raw_item(rec)
        assert item is not None
        assert getattr(item, "z0_platform", None) == "huggingface"

    def test_returns_none_when_title_empty(self):
        rec = _make_z0_record(title="")
        assert _z0_to_raw_item(rec) is None

    def test_returns_none_when_url_empty(self):
        rec = _make_z0_record(url="")
        assert _z0_to_raw_item(rec) is None

    def test_item_id_set(self):
        rec = _make_z0_record(z0_id="deadbeef12345678")
        item = _z0_to_raw_item(rec)
        assert item is not None
        assert item.item_id == "deadbeef12345678"

    def test_category_defaults_to_tech(self):
        rec = _make_z0_record(tag="official")
        item = _z0_to_raw_item(rec)
        assert item is not None
        assert item.source_category == "tech"


# ---------------------------------------------------------------------------
# load_z0_items — file I/O tests
# ---------------------------------------------------------------------------

class TestLoadZ0Items:
    def test_loads_multiple_items(self, tmp_path: Path):
        records = [
            _make_z0_record(title=f"Article {i}", url=f"https://example.com/{i}",
                            z0_id=f"id{i:016d}")
            for i in range(5)
        ]
        jsonl = tmp_path / "latest.jsonl"
        _write_jsonl(jsonl, records)

        items = load_z0_items(jsonl)
        assert len(items) == 5

    def test_all_items_are_raw_item(self, tmp_path: Path):
        records = [_make_z0_record(url=f"https://example.com/{i}", z0_id=f"id{i:016d}")
                   for i in range(3)]
        jsonl = tmp_path / "latest.jsonl"
        _write_jsonl(jsonl, records)

        items = load_z0_items(jsonl)
        for it in items:
            assert isinstance(it, RawItem)

    def test_skips_malformed_json_lines(self, tmp_path: Path):
        jsonl = tmp_path / "latest.jsonl"
        with jsonl.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(_make_z0_record()) + "\n")
            fh.write("{{BROKEN JSON\n")
            fh.write(json.dumps(_make_z0_record(url="https://example.com/2",
                                                z0_id="bbbbbbbbbbbbbbbb")) + "\n")

        items = load_z0_items(jsonl)
        assert len(items) == 2  # malformed line skipped

    def test_skips_records_without_url(self, tmp_path: Path):
        rec_good = _make_z0_record()
        rec_bad = _make_z0_record(url="", z0_id="cccccccccccccccc")
        jsonl = tmp_path / "latest.jsonl"
        _write_jsonl(jsonl, [rec_good, rec_bad])

        items = load_z0_items(jsonl)
        assert len(items) == 1

    def test_skips_records_without_title(self, tmp_path: Path):
        rec_good = _make_z0_record()
        rec_bad = _make_z0_record(title="", z0_id="dddddddddddddddd")
        jsonl = tmp_path / "latest.jsonl"
        _write_jsonl(jsonl, [rec_good, rec_bad])

        items = load_z0_items(jsonl)
        assert len(items) == 1

    def test_missing_file_returns_empty(self, tmp_path: Path):
        items = load_z0_items(tmp_path / "nonexistent.jsonl")
        assert items == []

    def test_empty_file_returns_empty(self, tmp_path: Path):
        jsonl = tmp_path / "latest.jsonl"
        jsonl.write_text("", encoding="utf-8")
        items = load_z0_items(jsonl)
        assert items == []

    def test_blank_lines_skipped(self, tmp_path: Path):
        jsonl = tmp_path / "latest.jsonl"
        with jsonl.open("w", encoding="utf-8") as fh:
            fh.write("\n")
            fh.write("   \n")
            fh.write(json.dumps(_make_z0_record()) + "\n")
            fh.write("\n")
        items = load_z0_items(jsonl)
        assert len(items) == 1

    def test_field_values_correct(self, tmp_path: Path):
        rec = _make_z0_record(
            title="Anthropic Claude 3.7 benchmarks",
            url="https://anthropic.com/blog/claude37",
            published_at="2026-02-15T12:00:00+00:00",
            platform="anthropic",
            feed_name="Anthropic Blog",
            frontier_score=95,
        )
        jsonl = tmp_path / "latest.jsonl"
        _write_jsonl(jsonl, [rec])

        items = load_z0_items(jsonl)
        assert len(items) == 1
        it = items[0]
        assert it.title == "Anthropic Claude 3.7 benchmarks"
        assert it.url == "https://anthropic.com/blog/claude37"
        assert it.published_at == "2026-02-15T12:00:00+00:00"
        assert it.source_name == "Anthropic Blog"
        assert getattr(it, "z0_frontier_score", None) == 95
        assert getattr(it, "z0_platform", None) == "anthropic"

    def test_unicode_title_preserved(self, tmp_path: Path):
        rec = _make_z0_record(title="OpenAI 發布 GPT-5，支援 100 萬 token 上下文")
        jsonl = tmp_path / "latest.jsonl"
        _write_jsonl(jsonl, [rec])

        items = load_z0_items(jsonl)
        assert len(items) == 1
        assert "GPT-5" in items[0].title
