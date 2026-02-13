"""Run the full pipeline once: Ingest -> Process -> Store -> Deliver."""

import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings
from core.ai_core import process_batch
from core.deep_analyzer import analyze_batch
from core.deep_delivery import write_deep_analysis
from core.delivery import print_console_summary, push_to_feishu, push_to_notion, write_digest
from core.ingestion import batch_items, dedup_items, fetch_all_feeds, filter_items
from core.storage import get_existing_item_ids, init_db, save_items, save_results
from core.notifications import send_all_notifications
from utils.entity_cleaner import clean_entities
from utils.logger import setup_logger
from utils.metrics import get_collector, reset_collector


def _apply_entity_cleaning(all_results: list) -> None:
    """Clean entities on all results and record metrics."""
    collector = get_collector()
    for r in all_results:
        a = r.schema_a
        before = len(a.entities)
        result = clean_entities(
            entities=a.entities,
            category=a.category,
            key_points=a.key_points,
            title=a.title_zh,
            body=a.summary_zh,
        )
        a.entities = result.cleaned
        collector.record_entity_cleaning(before, len(result.cleaned))


def run_pipeline() -> None:
    """Execute the full pipeline once."""
    log = setup_logger(settings.LOG_PATH)
    log.info("=" * 60)
    log.info("PIPELINE START")
    log.info("=" * 60)
    t_start = time.time()
    from datetime import UTC, datetime
    t_start_iso = datetime.now(UTC).isoformat()

    # Initialize metrics collector
    collector = reset_collector()
    collector.start()

    # Ensure DB exists
    init_db(settings.DB_PATH)

    # Z1: Ingestion & Preprocessing
    log.info("--- Z1: Ingestion & Preprocessing ---")
    raw_items = fetch_all_feeds()
    log.info("Fetched %d total raw items", len(raw_items))

    if not raw_items:
        log.warning("No items fetched from any feed. Exiting.")
        collector.stop()
        collector.write_json()
        send_all_notifications(t_start_iso, 0, True, "")
        return

    # Dedup against DB + within batch
    existing_ids = get_existing_item_ids(settings.DB_PATH)
    log.info("Existing items in DB: %d", len(existing_ids))
    deduped = dedup_items(raw_items, existing_ids)

    # Filter
    filtered = filter_items(deduped)

    if not filtered:
        log.warning("No items passed filters. Exiting.")
        collector.stop()
        collector.write_json()
        send_all_notifications(t_start_iso, 0, True, "")
        return

    # Save raw items to DB
    save_items(settings.DB_PATH, filtered)

    # Z2: AI Core (batch processing)
    log.info("--- Z2: AI Core ---")
    all_results = []
    for batch_num, batch in enumerate(batch_items(filtered), 1):
        log.info("Processing batch %d (%d items)", batch_num, len(batch))
        results = process_batch(batch)
        all_results.extend(results)

    # Entity cleaning (between extraction and deep analysis)
    _apply_entity_cleaning(all_results)

    # Update metrics
    collector.total_items = len(all_results)
    collector.passed_gate = sum(1 for r in all_results if r.passed_gate)

    # Z3: Storage & Delivery
    log.info("--- Z3: Storage & Delivery ---")
    save_results(settings.DB_PATH, all_results)

    # Local sink
    digest_path = write_digest(all_results)
    print_console_summary(all_results)

    # Optional sinks
    push_to_notion(all_results)
    push_to_feishu(all_results)

    # Z4: Deep Analysis (non-blocking)
    if settings.DEEP_ANALYSIS_ENABLED:
        passed_results = [r for r in all_results if r.passed_gate]
        if passed_results:
            try:
                log.info("--- Z4: Deep Analysis ---")
                report = analyze_batch(passed_results)
                deep_path = write_deep_analysis(report, metrics_md=collector.as_markdown())
                log.info("Deep analysis: %s", deep_path)
            except Exception as exc:
                log.error("Z4 Deep Analysis failed (non-blocking): %s", exc)
        else:
            log.info("Z4: No passed items, skipping deep analysis")
    else:
        log.info("Z4: Deep analysis disabled")

    # Finalize metrics
    collector.stop()
    metrics_path = collector.write_json()

    elapsed = time.time() - t_start
    passed = sum(1 for r in all_results if r.passed_gate)
    log.info("PIPELINE COMPLETE | %d processed | %d passed | %.2fs total", len(all_results), passed, elapsed)
    log.info("Digest: %s", digest_path)
    log.info("Metrics: %s", metrics_path)

    # Notifications
    send_all_notifications(t_start_iso, len(all_results), True, str(digest_path))


if __name__ == "__main__":
    run_pipeline()
