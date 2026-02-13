"""Tests for utils.entity_cleaner — noise reduction on entity lists."""

from __future__ import annotations

from utils.entity_cleaner import clean_entities


class TestEntityCleanerClimate:
    """Climate/energy category — Taklamakan should be kept, generic words removed."""

    def test_climate_keeps_taklamakan(self):
        result = clean_entities(
            entities=["Taklamakan", "Desert", "Sign", "Subscribe", "FAA", "https://x.y"],
            category="氣候/能源",
            key_points=[
                "China has planted trees around the Taklamakan Desert",
                "Scientists recorded a measurable change in local climate",
            ],
            title="Trees around the Taklamakan Desert",
        )
        assert "Taklamakan" in result.cleaned
        assert "FAA" in result.cleaned

    def test_climate_removes_ui_and_url(self):
        result = clean_entities(
            entities=["Taklamakan", "Desert", "Sign", "Subscribe", "FAA", "https://x.y"],
            category="氣候/能源",
            key_points=[
                "China has planted trees around the Taklamakan Desert",
                "Scientists recorded a measurable change in local climate",
            ],
            title="Trees around the Taklamakan Desert",
        )
        assert "Sign" not in result.cleaned
        assert "Subscribe" not in result.cleaned
        assert "https://x.y" not in result.cleaned

    def test_climate_desert_in_key_points(self):
        """Desert should be kept when category is 氣候/能源 AND key_points mention it."""
        result = clean_entities(
            entities=["Desert"],
            category="氣候/能源",
            key_points=["The Taklamakan Desert is shrinking"],
            title="Desert changes",
        )
        assert "Desert" in result.cleaned

    def test_climate_desert_not_in_context(self):
        """Desert removed when key_points don't mention it (even in 氣候/能源)."""
        result = clean_entities(
            entities=["Desert"],
            category="氣候/能源",
            key_points=["Solar panel efficiency improved by 20%"],
            title="Solar energy update",
        )
        assert "Desert" not in result.cleaned


class TestEntityCleanerTech:
    """Tech category — geographic and UI words should be removed."""

    def test_tech_removes_desert_sign_subscribe_url(self):
        result = clean_entities(
            entities=["Taklamakan", "Desert", "Sign", "Subscribe", "FAA", "https://x.y"],
            category="科技/技術",
        )
        assert "Desert" not in result.cleaned
        assert "Sign" not in result.cleaned
        assert "Subscribe" not in result.cleaned
        assert "https://x.y" not in result.cleaned

    def test_tech_keeps_faa(self):
        result = clean_entities(
            entities=["FAA", "Google", "AI"],
            category="科技/技術",
        )
        assert "FAA" in result.cleaned
        assert "Google" in result.cleaned
        assert "AI" in result.cleaned


class TestEdgeCases:
    def test_empty_entities(self):
        result = clean_entities(entities=[], category="科技/技術")
        assert result.cleaned == []
        assert result.removed == []

    def test_pure_numbers(self):
        result = clean_entities(entities=["123", "2026", "Google"], category="科技/技術")
        assert "123" not in result.cleaned
        assert "2026" not in result.cleaned
        assert "Google" in result.cleaned

    def test_single_char(self):
        result = clean_entities(entities=["A", "Google"], category="科技/技術")
        assert "A" not in result.cleaned
        assert "Google" in result.cleaned

    def test_unknown_short_acronym(self):
        """Unknown 3-letter all-caps tokens should be removed."""
        result = clean_entities(entities=["XYZ", "AI", "FAA"], category="科技/技術")
        assert "XYZ" not in result.cleaned
        assert "AI" in result.cleaned
        assert "FAA" in result.cleaned

    def test_debug_info(self):
        result = clean_entities(
            entities=["Sign", "123", "Google"],
            category="科技/技術",
        )
        assert "ui_word" in result.debug
        assert "Sign" in result.debug["ui_word"]
        assert "numeric_or_symbol" in result.debug
