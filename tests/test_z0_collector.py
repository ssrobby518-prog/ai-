"""Tests for core/z0_collector.py — no network, pure parse functions."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# Module under test
from core.z0_collector import (
    _extract_domain,
    _item_id,
    _parse_pubdate,
    _strip_html,
    compute_frontier_score,
    parse_feed,
)

# ---------------------------------------------------------------------------
# Inline RSS 2.0 sample
# ---------------------------------------------------------------------------

RSS_SAMPLE = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>AI Test Feed</title>
    <link>https://example.com</link>
    <description>Test AI news</description>
    <item>
      <title>OpenAI releases GPT-5 model with 10x improvements</title>
      <link>https://example.com/gpt5-release</link>
      <pubDate>Wed, 19 Feb 2026 09:00:00 +0000</pubDate>
      <description>OpenAI has launched GPT-5, a new LLM with 10x inference speed
        and improved benchmark scores on MMLU, HumanEval, and MATH.</description>
    </item>
    <item>
      <title>NVIDIA H200 GPU benchmark results published</title>
      <link>https://example.com/h200-benchmark</link>
      <pubDate>Tue, 18 Feb 2026 14:00:00 +0000</pubDate>
      <description>NVIDIA published H200 GPU performance benchmarks showing
        40% improvement over H100 in AI training workloads at $30k/unit.</description>
    </item>
    <item>
      <title>Weekly AI digest - subscribe now</title>
      <link>https://example.com/digest</link>
      <pubDate>Mon, 17 Feb 2026 08:00:00 +0000</pubDate>
      <description>Subscribe to our AI digest for weekly roundups.</description>
    </item>
  </channel>
</rss>
"""

# ---------------------------------------------------------------------------
# Inline Atom 1.0 sample
# ---------------------------------------------------------------------------

ATOM_SAMPLE = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>GitHub Releases — transformers</title>
  <id>https://github.com/huggingface/transformers/releases</id>
  <entry>
    <id>https://github.com/huggingface/transformers/releases/tag/v4.40.0</id>
    <title>transformers v4.40.0 — new vision-language models</title>
    <link rel="alternate" href="https://github.com/huggingface/transformers/releases/tag/v4.40.0"/>
    <published>2026-02-18T10:00:00Z</published>
    <updated>2026-02-18T10:05:00Z</updated>
    <summary>Release v4.40.0 adds LLaVA-Next, PaliGemma, and Idefics3.
      Benchmark improvements across vision-language tasks up to 15%.</summary>
  </entry>
  <entry>
    <id>https://github.com/huggingface/transformers/releases/tag/v4.39.3</id>
    <title>transformers v4.39.3 — bug fixes and performance patch</title>
    <link rel="alternate" href="https://github.com/huggingface/transformers/releases/tag/v4.39.3"/>
    <published>2026-02-10T08:00:00Z</published>
    <updated>2026-02-10T08:00:00Z</updated>
    <summary>Fixes critical inference bug in generation pipeline.
      Performance improvement 5% for batch inference scenarios.</summary>
  </entry>
</feed>
"""

# ---------------------------------------------------------------------------
# Inline Atom without namespace (GitHub-style variant)
# ---------------------------------------------------------------------------

ATOM_NO_NS_SAMPLE = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed>
  <title>GitHub Commits — vllm</title>
  <entry>
    <id>https://github.com/vllm-project/vllm/commit/abc123</id>
    <title>feat: add PagedAttention v3 for 2x throughput</title>
    <link rel="alternate" href="https://github.com/vllm-project/vllm/commit/abc123"/>
    <updated>2026-02-17T12:00:00Z</updated>
    <summary>Implements PagedAttention v3 achieving 2x inference throughput
      on A100 GPUs with 40GB VRAM.</summary>
  </entry>
</feed>
"""

_RSS_FEED_CFG = {
    "name": "Test RSS Feed",
    "url": "https://example.com/rss",
    "platform": "openai",
    "tag": "official",
}

_ATOM_FEED_CFG = {
    "name": "HuggingFace Releases",
    "url": "https://github.com/huggingface/transformers/releases.atom",
    "platform": "huggingface",
    "tag": "github_releases",
}

_ATOM_NO_NS_CFG = {
    "name": "vllm Commits",
    "url": "https://github.com/vllm-project/vllm/commits/main.atom",
    "platform": "vllm",
    "tag": "github_commits",
}


# ---------------------------------------------------------------------------
# parse_feed — RSS
# ---------------------------------------------------------------------------

class TestParseFeedRSS:
    def test_returns_at_least_two_items(self):
        items = parse_feed(RSS_SAMPLE, _RSS_FEED_CFG)
        assert len(items) >= 2, f"Expected >= 2 items, got {len(items)}"

    def test_title_parsed(self):
        items = parse_feed(RSS_SAMPLE, _RSS_FEED_CFG)
        titles = [it["title"] for it in items]
        assert any("GPT-5" in t or "OpenAI" in t for t in titles), titles

    def test_url_is_valid_http(self):
        items = parse_feed(RSS_SAMPLE, _RSS_FEED_CFG)
        for it in items:
            assert it["url"].startswith("http"), f"Bad URL: {it['url']}"

    def test_summary_non_empty(self):
        items = parse_feed(RSS_SAMPLE, _RSS_FEED_CFG)
        # At least one item should have a non-empty summary
        assert any(it["summary"] for it in items)

    def test_source_fields_set(self):
        items = parse_feed(RSS_SAMPLE, _RSS_FEED_CFG)
        for it in items:
            assert it["source"]["platform"] == "openai"
            assert it["source"]["feed_name"] == "Test RSS Feed"
            assert it["source"]["tag"] == "official"

    def test_frontier_score_is_int_0_to_100(self):
        items = parse_feed(RSS_SAMPLE, _RSS_FEED_CFG)
        for it in items:
            s = it["frontier_score"]
            assert isinstance(s, int), f"frontier_score not int: {s}"
            assert 0 <= s <= 100, f"frontier_score out of range: {s}"

    def test_id_is_hex_string(self):
        items = parse_feed(RSS_SAMPLE, _RSS_FEED_CFG)
        for it in items:
            assert len(it["id"]) == 16
            assert all(c in "0123456789abcdef" for c in it["id"])

    def test_empty_xml_returns_empty(self):
        assert parse_feed("", _RSS_FEED_CFG) == []

    def test_malformed_xml_returns_empty(self):
        assert parse_feed("<broken><</broken>", _RSS_FEED_CFG) == []

    def test_max_items_limit(self):
        items = parse_feed(RSS_SAMPLE, _RSS_FEED_CFG, max_items=1)
        assert len(items) <= 1


# ---------------------------------------------------------------------------
# parse_feed — Atom with namespace
# ---------------------------------------------------------------------------

class TestParseFeedAtom:
    def test_returns_two_items(self):
        items = parse_feed(ATOM_SAMPLE, _ATOM_FEED_CFG)
        assert len(items) == 2, f"Expected 2, got {len(items)}"

    def test_title_contains_version(self):
        items = parse_feed(ATOM_SAMPLE, _ATOM_FEED_CFG)
        titles = [it["title"] for it in items]
        assert any("v4.40" in t for t in titles), titles

    def test_url_is_github(self):
        items = parse_feed(ATOM_SAMPLE, _ATOM_FEED_CFG)
        for it in items:
            assert "github.com" in it["url"], it["url"]

    def test_published_at_is_iso(self):
        items = parse_feed(ATOM_SAMPLE, _ATOM_FEED_CFG)
        for it in items:
            if it["published_at"]:
                # Should parse as ISO datetime
                dt = datetime.fromisoformat(it["published_at"].replace("Z", "+00:00"))
                assert dt.year >= 2020

    def test_platform_propagated(self):
        items = parse_feed(ATOM_SAMPLE, _ATOM_FEED_CFG)
        for it in items:
            assert it["source"]["platform"] == "huggingface"


# ---------------------------------------------------------------------------
# parse_feed — Atom without namespace
# ---------------------------------------------------------------------------

class TestParseFeedAtomNoNS:
    def test_returns_one_item(self):
        items = parse_feed(ATOM_NO_NS_SAMPLE, _ATOM_NO_NS_CFG)
        assert len(items) >= 1

    def test_title_contains_feat(self):
        items = parse_feed(ATOM_NO_NS_SAMPLE, _ATOM_NO_NS_CFG)
        assert any("PagedAttention" in it["title"] or "feat" in it["title"].lower() for it in items)


# ---------------------------------------------------------------------------
# compute_frontier_score
# ---------------------------------------------------------------------------

class TestFrontierScore:
    def _make_item(self, platform: str = "openai", pub_hours_ago: float = 12.0,
                   title: str = "GPT-5 release benchmark", summary: str = "") -> dict:
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        pub = (now - timedelta(hours=pub_hours_ago)).isoformat()
        return {
            "title": title,
            "summary": summary,
            "published_at": pub,
            "source": {"platform": platform, "feed_name": "test", "feed_url": "", "tag": "official"},
        }

    def test_recent_official_ai_high_score(self):
        item = self._make_item(platform="openai", pub_hours_ago=2,
                               title="OpenAI releases GPT-5 model inference benchmark")
        score = compute_frontier_score(item)
        assert score >= 70, f"Expected >= 70, got {score}"

    def test_old_unknown_low_score(self):
        item = self._make_item(platform="unknown", pub_hours_ago=200,
                               title="Some random article")
        score = compute_frontier_score(item)
        assert score <= 40, f"Expected <= 40, got {score}"

    def test_score_range(self):
        item = self._make_item()
        score = compute_frontier_score(item)
        assert 0 <= score <= 100

    def test_no_published_still_returns_int(self):
        item = self._make_item()
        item["published_at"] = None
        score = compute_frontier_score(item)
        assert isinstance(score, int)
        assert 0 <= score <= 100


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_strip_html_removes_tags(self):
        assert "<b>" not in _strip_html("<b>hello</b>")
        assert "hello" in _strip_html("<b>hello</b>")

    def test_strip_html_unescape(self):
        result = _strip_html("&lt;b&gt;OpenAI&amp;NVIDIA&lt;/b&gt;")
        assert "&lt;" not in result
        assert "OpenAI" in result

    def test_strip_html_empty(self):
        assert _strip_html("") == ""
        assert _strip_html(None) == ""  # type: ignore[arg-type]

    def test_extract_domain(self):
        assert _extract_domain("https://www.openai.com/blog/gpt5") == "openai.com"
        assert _extract_domain("https://huggingface.co/blog/feed.xml") == "huggingface.co"

    def test_item_id_deterministic(self):
        a = _item_id("Test Title", "https://example.com/1")
        b = _item_id("Test Title", "https://example.com/1")
        assert a == b

    def test_item_id_different_inputs(self):
        a = _item_id("Title A", "https://example.com/1")
        b = _item_id("Title B", "https://example.com/1")
        assert a != b

    def test_parse_pubdate_rfc2822(self):
        result = _parse_pubdate("Wed, 19 Feb 2026 09:00:00 +0000")
        assert "2026" in result

    def test_parse_pubdate_iso(self):
        result = _parse_pubdate("2026-02-19T09:00:00Z")
        assert "2026" in result

    def test_parse_pubdate_empty(self):
        assert _parse_pubdate("") == ""


# ---------------------------------------------------------------------------
# collect_all (offline: empty config, no network needed)
# ---------------------------------------------------------------------------

class TestCollectAllOffline:
    def test_empty_config_produces_valid_output(self, tmp_path: Path):
        """collect_all with zero feeds must still write valid files."""
        from core.z0_collector import collect_all

        config = {
            "collector": {
                "locale": {"hl": "en-US", "gl": "US", "ceid": "US:en"},
                "time_window_days": 7,
                "http_timeout_sec": 1,
                "polite_delay_ms": 0,
                "max_items_per_feed": 5,
                "enable_fulltext_fetch": False,
                "user_agent": "test",
            },
            "official_feeds": [],
            "community_feeds": [],
            "github_watch": {"feeds": [], "repos": [], "wiki_probe": {"enabled": False}},
            "google_news_queries": [],
        }
        cfg_path = tmp_path / "cfg.json"
        cfg_path.write_text(json.dumps(config), encoding="utf-8")
        out_dir = tmp_path / "out"

        meta = collect_all(cfg_path, out_dir)

        assert (out_dir / "latest.jsonl").exists()
        assert (out_dir / "latest.meta.json").exists()
        assert isinstance(meta["total_items"], int)
        assert meta["total_items"] == 0

    def test_missing_config_returns_error_meta(self, tmp_path: Path):
        from core.z0_collector import collect_all

        meta = collect_all(tmp_path / "nonexistent.json", tmp_path / "out")
        assert "error" in meta

    def test_empty_config_meta_has_72h_fields(self, tmp_path: Path):
        """collect_all with zero feeds must produce meta with 72h stat fields."""
        from core.z0_collector import collect_all

        config = {
            "collector": {
                "locale": {"hl": "en-US", "gl": "US", "ceid": "US:en"},
                "time_window_days": 7,
                "http_timeout_sec": 1,
                "polite_delay_ms": 0,
                "max_items_per_feed": 5,
                "enable_fulltext_fetch": False,
                "user_agent": "test",
            },
            "official_feeds": [],
            "community_feeds": [],
            "github_watch": {"feeds": [], "repos": [], "wiki_probe": {"enabled": False}},
            "google_news_queries": [],
        }
        cfg_path = tmp_path / "cfg.json"
        cfg_path.write_text(json.dumps(config), encoding="utf-8")
        meta = collect_all(cfg_path, tmp_path / "out")

        assert "frontier_ge_70_72h" in meta
        assert "frontier_ge_85_72h" in meta
        assert meta["frontier_ge_70_72h"] == 0
        assert meta["frontier_ge_85_72h"] == 0


# ---------------------------------------------------------------------------
# Structure bonus (new: F-1)
# ---------------------------------------------------------------------------

class TestFrontierScoreStructureBonus:
    """Items with cutting-edge structural signals should score >= 85."""

    def _make_structured_item(
        self,
        title: str,
        summary: str,
        url: str = "https://example.com/item",
        platform: str = "openai",
        pub_hours_ago: float = 4.0,
    ) -> dict:
        now = datetime.now(timezone.utc)
        pub = (now - timedelta(hours=pub_hours_ago)).isoformat()
        return {
            "title": title,
            "summary": summary,
            "url": url,
            "published_at": pub,
            "published_at_parsed": pub,
            "source": {
                "platform": platform,
                "feed_name": "test",
                "feed_url": "",
                "tag": "official",
            },
            "content_text": "",
            "collected_at": now.isoformat(),
        }

    def test_arxiv_paper_high_score(self):
        """arXiv URL + benchmark mention + param count should yield >= 85."""
        item = self._make_structured_item(
            title="Scaling Laws for LLMs: 7B to 70B Parameter Study on MMLU",
            summary="We evaluate 7B, 13B, and 70B parameter models. "
                    "MMLU score improves from 68.2 to 87.4 with scale. open-source weights released.",
            url="https://arxiv.org/abs/2402.10055",
            platform="openai",
            pub_hours_ago=4.0,
        )
        score = compute_frontier_score(item)
        assert score >= 85, f"Expected >= 85 for arXiv paper with benchmark+params, got {score}"

    def test_github_release_version_tag_high_score(self):
        """GitHub release with version tag + release semantics should yield >= 85."""
        item = self._make_structured_item(
            title="openai-python v1.52.0 released",
            summary="Release v1.52.0: new model endpoint, weights checkpoint available, "
                    "open-source. See changelog for details.",
            url="https://github.com/openai/openai-python/releases/tag/v1.52.0",
            platform="openai",
            pub_hours_ago=2.0,
        )
        score = compute_frontier_score(item)
        assert score >= 85, f"Expected >= 85 for version-tag release item, got {score}"

    def test_benchmark_score_bonus(self):
        """Item explicitly naming a benchmark + numeric score should earn bonus."""
        item = self._make_structured_item(
            title="New model achieves 91.5% on HumanEval benchmark",
            summary="Our model scores 91.5% on HumanEval and 88.3 on MMLU. "
                    "Weights released as open-source checkpoint.",
            url="https://huggingface.co/blog/new-model",
            platform="huggingface",
            pub_hours_ago=6.0,
        )
        score = compute_frontier_score(item)
        assert score >= 85, f"Expected >= 85 for benchmark-score item, got {score}"

    def test_score_still_clamped_at_100(self):
        """Perfect item (all bonuses) must not exceed 100."""
        item = self._make_structured_item(
            title="arXiv:2402.10055 v1.0.0 MMLU 99% 70B MoE open-source weights released",
            summary="7B parameter model achieves MMLU 99% benchmark score. "
                    "Open-source checkpoint weights released. v1.0.0",
            url="https://arxiv.org/abs/2402.10055",
            platform="openai",
            pub_hours_ago=1.0,
        )
        score = compute_frontier_score(item)
        assert score == 100, f"Expected clamped to 100, got {score}"

    def test_no_structure_does_not_inflate(self):
        """Plain community item without any structure signals stays below 85."""
        now = datetime.now(timezone.utc)
        item = {
            "title": "Interesting discussion about AI pricing",
            "summary": "People are talking about the cost of AI tools.",
            "url": "https://reddit.com/r/artificial/post/abc",
            "published_at": (now - timedelta(hours=200)).isoformat(),
            "published_at_parsed": (now - timedelta(hours=200)).isoformat(),
            "source": {"platform": "unknown", "feed_name": "test", "feed_url": "", "tag": "community"},
            "content_text": "",
            "collected_at": now.isoformat(),
        }
        score = compute_frontier_score(item)
        assert score < 85, f"Expected < 85 for plain community item, got {score}"


# ---------------------------------------------------------------------------
# Published-at fallback (new: F-2)
# ---------------------------------------------------------------------------

_RSS_NO_PUBDATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>No-Date Feed</title>
    <link>https://example.com</link>
    <item>
      <title>AI model released with open-source weights</title>
      <link>https://example.com/ai-model-release</link>
      <description>An AI model release with great benchmark scores and open-source weights.</description>
    </item>
  </channel>
</rss>
"""

_ATOM_NO_PUBDATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>No-Date Atom</title>
  <entry>
    <id>https://example.com/entry/1</id>
    <title>transformers v4.40.0 released with open-source weights</title>
    <link rel="alternate" href="https://github.com/huggingface/transformers/releases/tag/v4.40.0"/>
    <summary>Release v4.40.0: new vision-language models. Benchmark MMLU 85.2%. Weights released.</summary>
  </entry>
</feed>
"""

_FEED_CFG_OPENAI = {
    "name": "Test OpenAI",
    "url": "https://example.com/rss",
    "platform": "openai",
    "tag": "official",
}


class TestPublishedAtFallback:
    """Verify auditable fallback when feed provides no date."""

    def test_rss_no_pubdate_source_is_fallback(self):
        items = parse_feed(_RSS_NO_PUBDATE, _FEED_CFG_OPENAI)
        assert len(items) == 1
        it = items[0]
        assert it["published_at_source"] == "fallback_collected_at", (
            f"Expected fallback_collected_at, got {it['published_at_source']}"
        )

    def test_rss_no_pubdate_parsed_is_none(self):
        items = parse_feed(_RSS_NO_PUBDATE, _FEED_CFG_OPENAI)
        assert items[0]["published_at_parsed"] is None

    def test_rss_no_pubdate_raw_is_empty(self):
        items = parse_feed(_RSS_NO_PUBDATE, _FEED_CFG_OPENAI)
        assert items[0]["published_at_raw"] == ""

    def test_rss_no_pubdate_recency_nonzero(self):
        """collected_at fallback → age ≈ 0 → recency bonus = 50 → score > 0."""
        items = parse_feed(_RSS_NO_PUBDATE, _FEED_CFG_OPENAI)
        score = compute_frontier_score(items[0])
        assert score > 0, "Expected positive score with collected_at fallback"

    def test_rss_no_pubdate_score_above_threshold(self):
        """openai platform + release semantics + collected_at → score >= 70."""
        items = parse_feed(_RSS_NO_PUBDATE, _FEED_CFG_OPENAI)
        score = compute_frontier_score(items[0])
        assert score >= 70, (
            f"Expected >= 70 (recency from collected_at + openai platform + keywords), got {score}"
        )

    def test_atom_no_pubdate_source_is_fallback(self):
        items = parse_feed(_ATOM_NO_PUBDATE, {
            "name": "HuggingFace Releases",
            "url": "https://github.com/huggingface/transformers/releases.atom",
            "platform": "huggingface",
            "tag": "github_releases",
        })
        assert len(items) == 1
        assert items[0]["published_at_source"] == "fallback_collected_at"

    def test_rss_with_pubdate_source_is_rss_pubdate(self):
        """When pubDate IS present the source label reflects the field used."""
        rss_with_date = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Dated Feed</title>
    <item>
      <title>GPT-5 release benchmark model</title>
      <link>https://openai.com/blog/gpt5</link>
      <pubDate>Wed, 19 Feb 2026 09:00:00 +0000</pubDate>
      <description>Released GPT-5 with benchmark improvements.</description>
    </item>
  </channel>
</rss>
"""
        items = parse_feed(rss_with_date, _FEED_CFG_OPENAI)
        assert len(items) == 1
        it = items[0]
        assert it["published_at_source"] == "rss_pubDate", (
            f"Expected rss_pubDate, got {it['published_at_source']}"
        )
        assert it["published_at_parsed"] is not None
        assert "2026" in str(it["published_at_parsed"])
