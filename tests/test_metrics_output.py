"""Tests for utils.metrics — collector and JSON output."""

from __future__ import annotations

import json
from pathlib import Path

from utils.metrics import EnrichStats, MetricsCollector, reset_collector


class TestEnrichStats:
    def test_success_rate(self):
        stats = EnrichStats()
        stats.record_success(0.5)
        stats.record_success(0.3)
        stats.record_fail("timeout", 1.0)
        assert stats.success == 2
        assert stats.fail == 1
        assert stats.attempted == 3
        assert abs(stats.success_rate - 66.7) < 1.0

    def test_latency_p50_p95(self):
        stats = EnrichStats()
        for lat in [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]:
            stats.record_success(lat)
        assert 0.5 <= stats.latency_p50 <= 0.6
        assert stats.latency_p95 >= 0.9

    def test_empty_stats(self):
        stats = EnrichStats()
        assert stats.success_rate == 0.0
        assert stats.latency_p50 == 0.0
        assert stats.latency_p95 == 0.0


class TestMetricsCollector:
    def test_full_lifecycle(self, tmp_path: Path):
        collector = MetricsCollector()
        collector.start()

        # Simulate enrichment
        collector.enrich_stats.record_success(0.5)
        collector.enrich_stats.record_success(0.3)
        collector.enrich_stats.record_fail("blocked", 0.1)

        # Simulate entity cleaning
        collector.record_entity_cleaning(before=10, after=7)
        collector.record_entity_cleaning(before=5, after=4)

        collector.total_items = 20
        collector.passed_gate = 15
        collector.stop()

        # Write to tmp_path
        path = collector.write_json(output_dir=tmp_path)
        assert path.exists()

        data = json.loads(path.read_text(encoding="utf-8"))

        # Verify all required fields
        required_fields = {
            "run_id",
            "timestamp",
            "total_items",
            "passed_gate",
            "total_runtime_seconds",
            "enrich_attempted",
            "enrich_success",
            "enrich_fail",
            "enrich_success_rate",
            "enrich_fail_reasons",
            "enrich_latency_p50",
            "enrich_latency_p95",
            "entity_before_count",
            "entity_after_count",
            "entity_noise_removed",
        }
        assert required_fields.issubset(data.keys()), f"Missing: {required_fields - set(data.keys())}"

        # Verify values
        assert data["total_items"] == 20
        assert data["passed_gate"] == 15
        assert data["enrich_attempted"] == 3
        assert data["enrich_success"] == 2
        assert data["enrich_fail"] == 1
        assert data["enrich_fail_reasons"] == {"blocked": 1}
        assert data["entity_before_count"] == 15
        assert data["entity_after_count"] == 11
        assert data["entity_noise_removed"] == 4
        assert data["total_runtime_seconds"] >= 0

    def test_markdown_output(self):
        collector = MetricsCollector()
        collector.start()
        collector.enrich_stats.record_success(0.5)
        collector.enrich_stats.record_fail("timeout", 1.0)
        collector.entity_noise_removed = 5
        collector.stop()

        md = collector.as_markdown()
        assert "## Run Metrics" in md
        assert "enrich_success_rate" in md
        assert "total_runtime_seconds" in md
        assert "entity_noise_removed" in md

    def test_reset_collector(self):
        c1 = reset_collector()
        c1.total_items = 99
        c2 = reset_collector()
        assert c2.total_items == 0
        assert c1.run_id != c2.run_id
