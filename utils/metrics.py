"""Lightweight pipeline metrics collector.

Collects timing, enrichment stats, and entity cleaning stats across a single
pipeline run and writes them to ``outputs/metrics.json``.
"""

from __future__ import annotations

import json
import os
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4


@dataclass
class EnrichStats:
    """Enrichment statistics returned by enrich_items / enrich_items_async."""

    attempted: int = 0
    success: int = 0
    fail: int = 0
    fail_reasons: dict[str, int] = field(default_factory=dict)
    latencies: list[float] = field(default_factory=list)

    def record_success(self, latency: float) -> None:
        self.attempted += 1
        self.success += 1
        self.latencies.append(latency)

    def record_fail(self, reason: str, latency: float) -> None:
        self.attempted += 1
        self.fail += 1
        self.fail_reasons[reason] = self.fail_reasons.get(reason, 0) + 1
        self.latencies.append(latency)

    @property
    def success_rate(self) -> float:
        return (self.success / self.attempted * 100) if self.attempted else 0.0

    @property
    def latency_p50(self) -> float:
        if not self.latencies:
            return 0.0
        return round(statistics.median(self.latencies), 3)

    @property
    def latency_p95(self) -> float:
        if len(self.latencies) < 2:
            return self.latency_p50
        sorted_lat = sorted(self.latencies)
        idx = int(len(sorted_lat) * 0.95)
        return round(sorted_lat[min(idx, len(sorted_lat) - 1)], 3)


class MetricsCollector:
    """Singleton-style collector for a single pipeline run."""

    def __init__(self) -> None:
        self.run_id: str = uuid4().hex[:12]
        self.timestamp: str = ""
        self._t_start: float = 0.0
        self.total_items: int = 0
        self.passed_gate: int = 0
        self.total_runtime_seconds: float = 0.0
        self.sources_total: int = 0
        self.sources_success: int = 0
        self.sources_failed: int = 0
        self.events_detected: int = 0
        self.signals_detected: int = 0
        self.corp_updates_detected: int = 0

        # Enrichment
        self.enrich_stats = EnrichStats()

        # Entity cleaning
        self.entity_before_count: int = 0
        self.entity_after_count: int = 0
        self.entity_noise_removed: int = 0

    def start(self) -> None:
        from datetime import UTC, datetime

        self._t_start = time.time()
        self.timestamp = datetime.now(UTC).isoformat()

    def stop(self) -> None:
        self.total_runtime_seconds = round(time.time() - self._t_start, 2)

    def record_entity_cleaning(self, before: int, after: int) -> None:
        self.entity_before_count += before
        self.entity_after_count += after
        self.entity_noise_removed += before - after

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "total_items": self.total_items,
            "passed_gate": self.passed_gate,
            "total_runtime_seconds": self.total_runtime_seconds,
            "sources_total": self.sources_total,
            "sources_success": self.sources_success,
            "sources_failed": self.sources_failed,
            "events_detected": self.events_detected,
            "signals_detected": self.signals_detected,
            "corp_updates_detected": self.corp_updates_detected,
            "enrich_attempted": self.enrich_stats.attempted,
            "enrich_success": self.enrich_stats.success,
            "enrich_fail": self.enrich_stats.fail,
            "enrich_success_rate": round(self.enrich_stats.success_rate, 1),
            "enrich_fail_reasons": self.enrich_stats.fail_reasons,
            "enrich_latency_p50": self.enrich_stats.latency_p50,
            "enrich_latency_p95": self.enrich_stats.latency_p95,
            "entity_before_count": self.entity_before_count,
            "entity_after_count": self.entity_after_count,
            "entity_noise_removed": self.entity_noise_removed,
        }

    def write_json(self, output_dir: str | Path | None = None) -> Path:
        """Write metrics to ``outputs/metrics.json``."""
        if output_dir is None:
            output_dir = os.getenv(
                "METRICS_OUTPUT_DIR",
                str(Path(__file__).resolve().parent.parent / "outputs"),
            )
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        path = out / "metrics.json"
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def as_markdown(self) -> str:
        """Return a Markdown snippet for appending to deep_analysis.md."""
        s = self.enrich_stats
        lines = [
            "",
            "---",
            "",
            "## Run Metrics",
            "",
            f"- **run_id**: `{self.run_id}`",
            f"- **enrich_success_rate**: {s.success_rate:.1f}% ({s.success}/{s.attempted})",
            f"- **total_runtime_seconds**: {self.total_runtime_seconds}",
            f"- **entity_noise_removed**: {self.entity_noise_removed}",
        ]
        if s.fail_reasons:
            top = sorted(s.fail_reasons.items(), key=lambda x: x[1], reverse=True)[:5]
            reasons = ", ".join(f"{k}: {v}" for k, v in top)
            lines.append(f"- **top_fail_reasons**: {reasons}")
        if s.latencies:
            lines.append(f"- **enrich_latency_p50**: {s.latency_p50}s")
            lines.append(f"- **enrich_latency_p95**: {s.latency_p95}s")
        lines.append("")
        return "\n".join(lines)


# Module-level singleton — reset per run via ``get_collector()``
_collector: MetricsCollector | None = None


def get_collector() -> MetricsCollector:
    """Return the current run's MetricsCollector (create if needed)."""
    global _collector
    if _collector is None:
        _collector = MetricsCollector()
    return _collector


def reset_collector() -> MetricsCollector:
    """Reset and return a fresh MetricsCollector (call at pipeline start)."""
    global _collector
    _collector = MetricsCollector()
    return _collector
