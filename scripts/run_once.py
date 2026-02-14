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
from core.education_renderer import (
    generate_binary_reports,
    generate_executive_reports,
    render_education_report,
    render_error_report,
    write_education_reports,
)
from core.ingestion import batch_items, dedup_items, fetch_all_feeds, filter_items
from core.notifications import send_all_notifications
from core.storage import get_existing_item_ids, init_db, save_items, save_results
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
    filtered, filter_summary = filter_items(deduped)

    # Build filter_summary dict for Z5
    filter_summary_dict: dict = {
        "input_count": filter_summary.input_count,
        "kept_count": filter_summary.kept_count,
        "dropped_by_reason": dict(filter_summary.dropped_by_reason),
    }

    all_results: list = []
    digest_path = None

    if filtered:
        # Save raw items to DB
        save_items(settings.DB_PATH, filtered)

        # Z2: AI Core (batch processing)
        log.info("--- Z2: AI Core ---")
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
    else:
        log.warning("No items passed filters — skipping Z2/Z3, proceeding to Z4/Z5.")
        digest_path = write_digest([])
        print_console_summary([])

    # Z4: Deep Analysis (non-blocking)
    z4_report = None  # 供 Z5 使用
    if settings.DEEP_ANALYSIS_ENABLED:
        passed_results = [r for r in all_results if r.passed_gate]
        if passed_results:
            try:
                log.info("--- Z4: Deep Analysis ---")
                z4_report = analyze_batch(passed_results)
                deep_path = write_deep_analysis(z4_report, metrics_md=collector.as_markdown())
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

    # Z5: Education Renderer (non-blocking, always runs)
    if settings.EDU_REPORT_ENABLED:
        try:
            log.info("--- Z5: Education Renderer ---")
            metrics_dict = collector.to_dict()
            # 模式 A（優先）：結構化輸入
            z5_results = all_results if all_results else None
            z5_report = z4_report
            # 模式 B fallback：讀取文本
            z5_text = None
            if z5_report is None:
                da_path = Path(settings.DEEP_ANALYSIS_OUTPUT_PATH)
                if da_path.exists():
                    z5_text = da_path.read_text(encoding="utf-8")

            notion_md, ppt_md, xmind_md = render_education_report(
                results=z5_results,
                report=z5_report,
                metrics=metrics_dict,
                deep_analysis_text=z5_text,
                max_items=settings.EDU_REPORT_MAX_ITEMS,
                filter_summary=filter_summary_dict,
            )
            edu_paths = write_education_reports(notion_md, ppt_md, xmind_md)
            log.info("Z5: 教育版報告已生成 → %s", [str(p) for p in edu_paths])

            # Generate executive output files (PPTX + DOCX + Notion + XMind)
            try:
                pptx_path, docx_path, notion_path, xmind_path = generate_executive_reports(
                    results=z5_results,
                    report=z5_report,
                    metrics=metrics_dict,
                    deep_analysis_text=z5_text,
                    max_items=settings.EDU_REPORT_MAX_ITEMS,
                )
                log.info("Executive PPTX generated: %s", pptx_path)
                log.info("Executive DOCX generated: %s", docx_path)
                log.info("Notion page generated: %s", notion_path)
                log.info("XMind mindmap generated: %s", xmind_path)
            except Exception as exc_bin:
                log.error("Executive report generation failed (non-blocking): %s", exc_bin)
        except Exception as exc:
            log.error("Z5 Education Renderer failed (non-blocking): %s", exc)
            try:
                err_md = render_error_report(exc)
                err_path = Path(settings.PROJECT_ROOT) / "outputs" / "deep_analysis_education.md"
                err_path.parent.mkdir(parents=True, exist_ok=True)
                err_path.write_text(err_md, encoding="utf-8")
                log.info("Z5: 錯誤說明已寫入 %s", err_path)
            except Exception:
                log.error("Z5: 連錯誤報告都寫不出來")
    else:
        log.info("Z5: Education report disabled")

    elapsed = time.time() - t_start
    passed = sum(1 for r in all_results if r.passed_gate)
    log.info("PIPELINE COMPLETE | %d processed | %d passed | %.2fs total", len(all_results), passed, elapsed)
    log.info("Digest: %s", digest_path)
    log.info("Metrics: %s", metrics_path)

    # Notifications
    send_all_notifications(t_start_iso, len(all_results), True, str(digest_path))


if __name__ == "__main__":
    run_pipeline()
