"""Filter Gate Observability tests.

Covers:
- Calibration profile overrides
- FilterSummary per-reason counts
- Z5 empty render with filter_summary
- FILTER_SUMMARY greppable log line
"""

import logging
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.ingestion import filter_items
from schemas.models import RawItem

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_item(
    item_id: str = "test_001",
    title: str = "AI industry update",
    body: str = (
        ("The platform team shipped a new distributed inference stack for enterprise traffic. " * 10)
        + ("Operators validated rollout metrics across regions and confirmed stable latency. " * 10)
        + ("Customers reported improved throughput while preserving governance and compliance controls. " * 10)
    ),
    published_hours_ago: int = 1,
    lang: str = "en",
) -> RawItem:
    pub = (datetime.now(UTC) - timedelta(hours=published_hours_ago)).isoformat()
    return RawItem(
        item_id=item_id,
        title=title,
        url=f"https://example.com/{item_id}",
        body=body,
        published_at=pub,
        source_name="test",
        source_category="tech",
        lang=lang,
    )


# ---------------------------------------------------------------------------
# TestCalibrationProfile
# ---------------------------------------------------------------------------


class TestCalibrationProfile:
    """Verify calibration overrides take effect when RUN_PROFILE=calibration."""

    def test_calibration_overrides_applied(self):
        env = {
            "RUN_PROFILE": "calibration",
            # Clear any explicit overrides so calibration defaults kick in
            "GATE_MIN_SCORE": "",
            "MIN_BODY_LENGTH": "",
            "NEWER_THAN_HOURS": "",
            "GATE_MAX_DUP_RISK": "",
        }
        with patch.dict(os.environ, env, clear=False):
            # Re-import to trigger module-level if-block
            import importlib

            from config import settings
            importlib.reload(settings)

            assert settings.RUN_PROFILE == "calibration"
            assert settings.GATE_MIN_SCORE == 5.5
            assert settings.MIN_BODY_LENGTH == 80
            assert settings.NEWER_THAN_HOURS == 72
            assert settings.GATE_MAX_DUP_RISK == 0.40

        # Restore prod defaults
        with patch.dict(os.environ, {"RUN_PROFILE": "prod"}, clear=False):
            importlib.reload(settings)

    def test_prod_profile_unchanged(self):
        import importlib

        from config import settings

        with patch.dict(os.environ, {"RUN_PROFILE": "prod"}, clear=False):
            importlib.reload(settings)
            assert settings.RUN_PROFILE == "prod"
            # Prod defaults from original settings
            assert settings.NEWER_THAN_HOURS == 24
            assert settings.MIN_BODY_LENGTH == 120

    def test_calibration_env_var_override_wins(self):
        """If user sets both RUN_PROFILE=calibration AND a specific env var, env var wins."""
        env = {
            "RUN_PROFILE": "calibration",
            "MIN_BODY_LENGTH": "50",
        }
        import importlib

        from config import settings

        with patch.dict(os.environ, env, clear=False):
            importlib.reload(settings)
            assert settings.MIN_BODY_LENGTH == 50  # env var wins over calibration default

        with patch.dict(os.environ, {"RUN_PROFILE": "prod", "MIN_BODY_LENGTH": ""}, clear=False):
            importlib.reload(settings)


# ---------------------------------------------------------------------------
# TestFilterSummary
# ---------------------------------------------------------------------------


class TestFilterSummary:
    """Call filter_items() with controlled RawItems, verify summary counts."""

    def test_all_pass(self):
        items = [_make_item(item_id=f"pass_{i}") for i in range(3)]
        result, summary = filter_items(items)
        assert len(result) == 3
        assert summary.input_count == 3
        assert summary.kept_count == 3
        assert sum(summary.dropped_by_reason.values()) == 0

    def test_too_old(self):
        items = [_make_item(published_hours_ago=200)]
        result, summary = filter_items(items)
        assert len(result) == 0
        assert summary.dropped_by_reason.get("too_old", 0) == 1

    def test_body_too_short(self):
        items = [_make_item(body="short")]
        result, summary = filter_items(items)
        assert len(result) == 0
        assert summary.dropped_by_reason.get("body_too_short", 0) == 1

    def test_mixed_reasons(self):
        items = [
            _make_item(item_id="old", published_hours_ago=200),
            _make_item(item_id="short", body="x"),
            _make_item(item_id="good"),
        ]
        _, summary = filter_items(items)
        assert summary.kept_count == 1
        assert summary.dropped_by_reason.get("too_old", 0) == 1
        assert summary.dropped_by_reason.get("body_too_short", 0) == 1

    def test_empty_input(self):
        _, summary = filter_items([])
        assert summary.input_count == 0
        assert summary.kept_count == 0
        assert summary.dropped_by_reason == {}


# ---------------------------------------------------------------------------
# TestZeroItemsZ5Render
# ---------------------------------------------------------------------------


class TestZeroItemsZ5Render:
    """Call render_education_report with empty results + filter_summary."""

    def test_empty_report_contains_filter_table(self):
        from core.education_renderer import render_education_report

        fs = {
            "input_count": 40,
            "kept_count": 0,
            "dropped_by_reason": {
                "too_old": 15,
                "lang_not_allowed": 5,
                "keyword_mismatch": 10,
                "body_too_short": 10,
            },
        }
        notion_md, _, _ = render_education_report(
            results=None,
            metrics={},
            filter_summary=fs,
        )
        assert "本次無有效新聞" in notion_md
        assert "時間過舊" in notion_md
        assert "語言不符" in notion_md
        assert "關鍵字不符" in notion_md
        assert "內文過短" in notion_md
        assert "calibration" in notion_md
        assert "封面資訊" in notion_md  # still has cover

    def test_empty_report_has_timestamp(self):
        from core.education_renderer import render_education_report

        notion_md, _, _ = render_education_report(
            results=None,
            metrics={},
            filter_summary={"input_count": 0, "kept_count": 0, "dropped_by_reason": {}},
        )
        assert "報告時間" in notion_md

    def test_nonempty_report_no_filter_section(self):
        """When cards exist, the filter empty section should NOT appear."""
        import importlib
        import sys

        test_mod_path = str(Path(__file__).resolve().parent / "test_education_renderer.py")
        spec = importlib.util.spec_from_file_location("test_education_renderer", test_mod_path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["test_education_renderer"] = mod
        spec.loader.exec_module(mod)
        _render_all = mod._render_all

        notion_md, _, _ = _render_all()
        assert "本次無有效新聞" not in notion_md


# ---------------------------------------------------------------------------
# TestFilterSummaryInLog
# ---------------------------------------------------------------------------


class TestFilterSummaryInLog:
    """Verify log output contains greppable FILTER_SUMMARY line."""

    def test_filter_summary_logged(self, caplog):
        items = [
            _make_item(item_id="old", published_hours_ago=200),
            _make_item(item_id="good"),
        ]
        with caplog.at_level(logging.INFO):
            _, _ = filter_items(items)

        filter_log = [r for r in caplog.records if "FILTER_SUMMARY" in r.message]
        assert len(filter_log) == 1
        assert "kept=1" in filter_log[0].message
        assert "dropped_total=1" in filter_log[0].message
