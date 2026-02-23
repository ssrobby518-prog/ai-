"""Run the full pipeline once: Ingest -> Process -> Store -> Deliver."""

import os
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings
from core.ai_core import process_batch
from core.content_strategy import (
    build_corp_watch_summary,
    build_signal_summary,
    is_non_event_or_index,
)
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
from core.info_density import apply_density_gate
from core.notifications import send_all_notifications
from core.storage import get_existing_item_ids, init_db, save_items, save_results
from schemas.education_models import EduNewsCard
from schemas.models import RawItem
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


def _build_quality_cards(
    all_results: list,
    source_url_map: dict[str, str] | None = None,
) -> list[EduNewsCard]:
    """Build lightweight EduNewsCard objects for v5.2 metrics aggregation."""
    cards: list[EduNewsCard] = []
    source_url_map = source_url_map or {}
    for r in all_results:
        a = r.schema_a
        b = r.schema_b
        c = r.schema_c
        source_url = str(getattr(c, "cta_url", "") or "").strip()
        if not source_url.startswith(("http://", "https://")):
            fallback_url = str(source_url_map.get(str(r.item_id), "") or "").strip()
            if fallback_url.startswith(("http://", "https://")):
                source_url = fallback_url
        cards.append(
            EduNewsCard(
                item_id=str(r.item_id),
                is_valid_news=bool(getattr(r, "passed_gate", False)),
                invalid_reason="" if bool(getattr(r, "passed_gate", False)) else "failed_gate",
                title_plain=str(a.title_zh or ""),
                what_happened=str(a.summary_zh or ""),
                why_important=str(a.summary_zh or ""),
                source_name=str(a.source_id or ""),
                source_url=source_url if source_url.startswith(("http://", "https://")) else "",
                category=str(a.category or ""),
                final_score=float(getattr(b, "final_score", 0.0) or 0.0),
            )
        )
    return cards


def _build_soft_quality_cards_from_filtered(filtered_items: list) -> list[EduNewsCard]:
    """Build fallback cards from post-gate RawItems when AI results are empty."""
    cards: list[EduNewsCard] = []
    for item in filtered_items:
        title = str(getattr(item, "title", "") or "").strip() or "來源訊號"
        body = str(getattr(item, "body", "") or "").strip()
        summary = body[:260] if body else "來源內容有限，請以原始連結核對。"
        source_name = str(getattr(item, "source_name", "") or "").strip() or "來源平台"
        source_url = str(getattr(item, "url", "") or "").strip()
        density = float(getattr(item, "density_score", 0) or 0)
        score = max(3.0, min(10.0, round(density / 10.0, 2)))
        card = EduNewsCard(
            item_id=str(getattr(item, "item_id", "") or ""),
            is_valid_news=True,
            title_plain=title,
            what_happened=summary,
            why_important=f"來源：{source_name}。原始連結：{source_url if source_url.startswith('http') else 'N/A'}。",
            source_name=source_name,
            source_url=source_url if source_url.startswith("http") else "",
            category=str(getattr(item, "source_category", "") or "tech"),
            final_score=score,
        )
        try:
            setattr(card, "event_gate_pass", bool(getattr(item, "event_gate_pass", True)))
            setattr(card, "signal_gate_pass", bool(getattr(item, "signal_gate_pass", True)))
            setattr(card, "density_score", int(density))
            setattr(card, "density_tier", "A" if bool(getattr(item, "event_gate_pass", False)) else "B")
        except Exception:
            pass
        cards.append(card)
    return cards


def _select_processing_items(
    filtered_items: list[RawItem],
    signal_pool: list[RawItem],
    *,
    fallback_limit: int = 3,
    include_signal_context: bool = False,
    signal_context_limit: int = 0,
) -> tuple[list[RawItem], bool]:
    """Select items for downstream Z2/Z3 processing.

    Priority:
    1) event-gate-passed `filtered_items`
    2) when event gate is empty but signal gate has candidates, use a small
       signal fallback slice to avoid all-empty runs.
    """
    if filtered_items:
        selected = list(filtered_items)
        if include_signal_context and signal_pool:
            remaining = max(0, int(signal_context_limit))
            existing_ids = {str(getattr(it, "item_id", "") or "") for it in selected}
            for item in signal_pool:
                if remaining <= 0:
                    break
                item_id = str(getattr(item, "item_id", "") or "")
                if item_id and item_id in existing_ids:
                    continue
                try:
                    setattr(item, "event_gate_pass", False)
                    setattr(item, "signal_gate_pass", True)
                    setattr(item, "low_confidence", True)
                except Exception:
                    pass
                selected.append(item)
                existing_ids.add(item_id)
                remaining -= 1
        return selected, False

    if not signal_pool:
        return [], False

    limit = max(1, int(fallback_limit))
    selected = list(signal_pool[:limit])
    for item in selected:
        try:
            setattr(item, "event_gate_pass", False)
            setattr(item, "signal_gate_pass", True)
            setattr(item, "low_confidence", True)
        except Exception:
            pass
    return selected, True


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
    # Z0 mode: load from local JSONL when Z0_ENABLED=True and file exists
    _z0_enabled = bool(getattr(settings, "Z0_ENABLED", False))
    _z0_path = Path(getattr(settings, "Z0_INPUT_PATH", settings.PROJECT_ROOT / "data/raw/z0/latest.jsonl"))
    if not Path(_z0_path).is_absolute():
        _z0_path = Path(settings.PROJECT_ROOT) / _z0_path
    if _z0_enabled and Path(_z0_path).exists():
        try:
            from core.z0_loader import load_z0_items
            raw_items = load_z0_items(Path(_z0_path))
            log.info("Z0 mode: loaded %d items from %s", len(raw_items), _z0_path)
        except Exception as _z0_exc:
            log.warning("Z0 load failed (%s); falling back to online fetch", _z0_exc)
            raw_items = fetch_all_feeds()
        # Z0 mode: fulltext hydration (normally runs inside fetch_all_feeds; must run here too)
        try:
            from utils.fulltext_hydrator import hydrate_items_batch
            raw_items = hydrate_items_batch(raw_items)
            log.info("Z0 fulltext hydration complete (%d items)", len(raw_items))
        except Exception as _z0_hydr_exc:
            log.warning("Z0 fulltext hydration failed (non-fatal): %s", _z0_hydr_exc)
    else:
        raw_items = fetch_all_feeds()
    log.info("Fetched %d total raw items", len(raw_items))
    collector.fetched_total = len(raw_items)
    # fetch_all_feeds() already returns normalized + enrichment-applied items.
    collector.normalized_total = len(raw_items)
    collector.enriched_total = len(raw_items)
    log.info(
        "INGEST_COUNTS fetched_total=%d normalized_total=%d enriched_total=%d",
        collector.fetched_total,
        collector.normalized_total,
        collector.enriched_total,
    )

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
    collector.deduped_total = len(deduped)
    log.info("INGEST_COUNTS deduped_total=%d", collector.deduped_total)

    # Filter
    filtered, filter_summary = filter_items(deduped)
    signal_pool = list(filter_summary.signal_pool or [])
    gate_stats = dict(filter_summary.gate_stats or {})
    collector.event_gate_pass_total = int(gate_stats.get("event_gate_pass_total", gate_stats.get("gate_pass_total", filter_summary.kept_count)))
    collector.signal_gate_pass_total = int(gate_stats.get("signal_gate_pass_total", len(signal_pool)))
    collector.gate_pass_total = collector.event_gate_pass_total
    collector.hard_pass_total = int(gate_stats.get("hard_pass_total", gate_stats.get("passed_strict", collector.event_gate_pass_total)))
    collector.soft_pass_total = int(gate_stats.get("soft_pass_total", gate_stats.get("passed_relaxed", max(collector.signal_gate_pass_total - collector.event_gate_pass_total, 0))))
    collector.gate_reject_total = int(gate_stats.get("gate_reject_total", 0))
    collector.rejected_total = int(gate_stats.get("rejected_total", collector.gate_reject_total))
    processing_items, used_signal_fallback = _select_processing_items(
        filtered,
        signal_pool,
        fallback_limit=3,
        include_signal_context=True,
        signal_context_limit=max(0, 3 - len(filtered)),
    )
    collector.after_filter_total = len(processing_items)
    collector.rejected_reason_top = list(gate_stats.get("rejected_reason_top", []))
    collector.density_score_top5 = list(gate_stats.get("density_score_top5", []))
    log.info(
        "INGEST_COUNTS fetched_total=%d event_gate_pass_total=%d signal_gate_pass_total=%d gate_reject_total=%d after_filter_total=%d rejected_reason_top=%s",
        collector.fetched_total,
        collector.event_gate_pass_total,
        collector.signal_gate_pass_total,
        collector.gate_reject_total,
        collector.after_filter_total,
        collector.rejected_reason_top,
    )
    log.info(
        "INGEST_COUNTS hard_pass_total=%d soft_pass_total=%d gate_pass_total=%d density_score_top5=%s",
        collector.hard_pass_total,
        collector.soft_pass_total,
        collector.gate_pass_total,
        collector.density_score_top5,
    )

    # Build filter_summary dict for Z5
    if used_signal_fallback:
        log.warning(
            "Event gate kept 0 items; using %d signal-gate items for downstream processing.",
            len(processing_items),
        )

    filter_summary_dict: dict = {
        "input_count": filter_summary.input_count,
        "kept_count": filter_summary.kept_count,
        "processing_count": len(processing_items),
        "dropped_by_reason": dict(filter_summary.dropped_by_reason),
    }

    # Z0 extra cards pool (B) — built from high-frontier signal_pool items; populated later
    z0_exec_extra_cards: list[EduNewsCard] = []

    all_results: list = []
    digest_path = None

    if processing_items:
        # Save raw items to DB
        save_items(settings.DB_PATH, processing_items)

        # Z2: AI Core (batch processing)
        log.info("--- Z2: AI Core ---")
        for batch_num, batch in enumerate(batch_items(processing_items), 1):
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
        log.warning("No items passed event/signal gates — skipping Z2/Z3, proceeding to Z4/Z5.")
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

    source_url_map = {
        str(getattr(item, "item_id", "") or ""): str(getattr(item, "url", "") or "")
        for item in processing_items
    }
    quality_cards = _build_quality_cards(all_results, source_url_map=source_url_map)
    if not quality_cards and signal_pool:
        quality_cards = _build_soft_quality_cards_from_filtered(signal_pool)
    if not quality_cards and filtered:
        quality_cards = _build_soft_quality_cards_from_filtered(filtered)
    density_candidates = [c for c in quality_cards if c.is_valid_news]
    event_candidates = [c for c in density_candidates if not is_non_event_or_index(c)]

    event_density_cards, _event_rejected, event_density_stats, _ = apply_density_gate(
        event_candidates, "event"
    )
    _signal_density_cards, _signal_rejected, signal_density_stats, _ = apply_density_gate(
        density_candidates, "signal"
    )
    _corp_density_cards, _corp_rejected, corp_density_stats, _ = apply_density_gate(
        density_candidates, "corp"
    )

    collector.density_total_in = event_density_stats.total_in
    collector.density_passed = event_density_stats.passed
    collector.density_rejected = event_density_stats.rejected_total
    collector.density_avg_score = event_density_stats.avg_score
    collector.density_rejected_reason_top = list(event_density_stats.rejected_reason_top)

    log.info(
        "INFO_DENSITY[event] total_in=%d passed=%d rejected=%d avg_score=%.2f reasons_top=%s",
        event_density_stats.total_in,
        event_density_stats.passed,
        event_density_stats.rejected_total,
        event_density_stats.avg_score,
        event_density_stats.rejected_reason_top,
    )
    log.info(
        "INFO_DENSITY[signal] total_in=%d passed=%d rejected=%d avg_score=%.2f reasons_top=%s",
        signal_density_stats.total_in,
        signal_density_stats.passed,
        signal_density_stats.rejected_total,
        signal_density_stats.avg_score,
        signal_density_stats.rejected_reason_top,
    )
    log.info(
        "INFO_DENSITY[corp] total_in=%d passed=%d rejected=%d avg_score=%.2f reasons_top=%s",
        corp_density_stats.total_in,
        corp_density_stats.passed,
        corp_density_stats.rejected_total,
        corp_density_stats.avg_score,
        corp_density_stats.rejected_reason_top,
    )

    collector.events_detected = len(event_density_cards)

    signal_summary = build_signal_summary(quality_cards)
    collector.signals_detected = len(signal_summary)

    corp_summary = build_corp_watch_summary(quality_cards, metrics=collector.to_dict())
    collector.corp_updates_detected = int(corp_summary.get("updates", 0))
    log.info(
        "STRATEGY_COUNTS event_candidates_total=%d signals_total=%d corp_mentions_total=%d",
        collector.events_detected,
        collector.signals_detected,
        int(corp_summary.get("mentions_count", corp_summary.get("total_mentions", 0))),
    )

    # Finalize metrics
    collector.stop()
    metrics_path = collector.write_json()

    # (B) Build Z0 extra cards: inject high-frontier signal_pool items into the
    # executive deck so select_executive_items() has enough candidates to meet
    # product/tech/business quotas (fixes events_total=1 bottleneck).
    # Two-track channel gate:
    #   Track A (standard): frontier >= Z0_EXEC_MIN_FRONTIER (65) — any channel
    #   Track B (business-relaxed): frontier >= Z0_EXEC_MIN_FRONTIER_BIZ (45)
    #           — only when best_channel=="business" AND business_score >= threshold
    #     Rationale: business news from aggregators (google_news) gets +4 platform
    #     bonus vs +20 for official feeds, so fresh funding/M&A articles cap out at
    #     ~64 frontier and are silently excluded by Track A alone.  Track B ensures
    #     the business quota in select_executive_items() can be filled reliably.
    _z0_exec_min_frontier = int(getattr(settings, "Z0_EXEC_MIN_FRONTIER", 65))
    _z0_exec_min_frontier_biz = int(getattr(settings, "Z0_EXEC_MIN_FRONTIER_BIZ", 45))
    _z0_exec_max_extra = int(getattr(settings, "Z0_EXEC_MAX_EXTRA", 50))
    _z0_exec_min_channel = int(getattr(settings, "Z0_EXEC_MIN_CHANNEL", 55))
    # Audit counters — written to z0_injection.meta.json at end of block
    _z0_inject_candidates_total = 0
    _z0_inject_after_frontier_total = 0
    _z0_inject_after_channel_gate_total = 0
    _z0_inject_selected_total = 0
    _z0_inject_dropped_by_channel_gate = 0

    if _z0_enabled and signal_pool:
        from utils.topic_router import classify_channels as _classify_channels

        _z0_inject_candidates_total = len(signal_pool)

        # Step 1: Two-track frontier pool construction
        # Track A: any channel, strict frontier
        _track_a_ids: set[str] = set()
        _track_a: list = []
        for _it in signal_pool:
            _fs = int(getattr(_it, "z0_frontier_score", 0) or 0)
            if _fs >= _z0_exec_min_frontier:
                _iid = str(getattr(_it, "item_id", "") or id(_it))
                _track_a_ids.add(_iid)
                _track_a.append(_it)

        # Tracks B + C: quota supplements from FULL deduped pool (not just signal_pool).
        # Rationale: official product-announcement sources (OpenAI, Anthropic) and
        # google_news business articles both tend to have short RSS summaries (<300 chars)
        # that fail the body-length signal gate, so they never reach signal_pool / Track A.
        # Track B = best_channel=="business" AND business_score >= threshold
        # Track C = best_channel=="product"  AND product_score  >= threshold
        # Both search deduped (all z0 items after DB dedup, body_too_short included).
        _track_b: list = []
        _track_c: list = []
        _z0_deduped_supp_pool = deduped  # all z0 items after DB dedup
        for _it in _z0_deduped_supp_pool:
            _fs = int(getattr(_it, "z0_frontier_score", 0) or 0)
            if _fs < _z0_exec_min_frontier_biz:
                continue  # below relaxed threshold (shared by both Track B and C)
            _iid = str(getattr(_it, "item_id", "") or id(_it))
            if _iid in _track_a_ids:
                continue  # already in Track A
            _text = f"{getattr(_it, 'title', '') or ''} {getattr(_it, 'body', '') or ''}"
            _url = str(getattr(_it, "url", "") or "")
            _ch_bc = _classify_channels(_text, _url)
            if _ch_bc["best_channel"] == "business" and _ch_bc["business_score"] >= _z0_exec_min_channel:
                _track_b.append(_it)
            elif _ch_bc["best_channel"] == "product" and _ch_bc["product_score"] >= _z0_exec_min_channel:
                _track_c.append(_it)

        # Merge tracks: sort each by frontier descending, Track A first (higher quality)
        _track_a.sort(key=lambda it: int(getattr(it, "z0_frontier_score", 0) or 0), reverse=True)
        _track_b.sort(key=lambda it: int(getattr(it, "z0_frontier_score", 0) or 0), reverse=True)
        _track_c.sort(key=lambda it: int(getattr(it, "z0_frontier_score", 0) or 0), reverse=True)
        _frontier_pool = _track_a + _track_b + _track_c
        _z0_inject_after_frontier_total = len(_frontier_pool)

        # Step 2: channel gate — max(product, tech, business) >= threshold; dev excluded
        # Supplement items (B/C) already satisfy their respective channel_score >= threshold,
        # but we run the same gate for consistency (they will pass).
        def _passes_channel_gate(it) -> bool:
            text = f"{getattr(it, 'title', '') or ''} {getattr(it, 'body', '') or ''}"
            url = str(getattr(it, "url", "") or "")
            ch = _classify_channels(text, url)
            return max(ch["product_score"], ch["tech_score"], ch["business_score"]) >= _z0_exec_min_channel

        _channel_passed = [it for it in _frontier_pool if _passes_channel_gate(it)]
        _z0_inject_after_channel_gate_total = len(_channel_passed)
        _z0_inject_dropped_by_channel_gate = _z0_inject_after_frontier_total - _z0_inject_after_channel_gate_total

        # Step 3: Additive supplement selection (no dev backfill).
        # Track A gets its FULL max_extra budget (maintains channel diversity).
        # Tracks B (business) and C (product) are appended as supplements so
        # select_executive_items() can fill both business >= 2 and product >= 2 quotas
        # even when signal_pool lacks these channels.
        _Z0_BIZ_RESERVE = 4   # 2× exec business quota target
        _Z0_PROD_RESERVE = 4  # 2× exec product quota target
        _track_b_id_set = {str(getattr(_it2, "item_id", "") or id(_it2)) for _it2 in _track_b}
        _track_c_id_set = {str(getattr(_it2, "item_id", "") or id(_it2)) for _it2 in _track_c}
        _ch_pass_b = [_it2 for _it2 in _channel_passed
                      if str(getattr(_it2, "item_id", "") or id(_it2)) in _track_b_id_set]
        _ch_pass_c = [_it2 for _it2 in _channel_passed
                      if str(getattr(_it2, "item_id", "") or id(_it2)) in _track_c_id_set]
        _ch_pass_a = [_it2 for _it2 in _channel_passed
                      if str(getattr(_it2, "item_id", "") or id(_it2))
                      not in (_track_b_id_set | _track_c_id_set)]
        # Track A fills full budget; Track B and C appended as supplements
        _selected_items = (
            _ch_pass_a[:_z0_exec_max_extra]
            + _ch_pass_b[:_Z0_BIZ_RESERVE]
            + _ch_pass_c[:_Z0_PROD_RESERVE]
        )
        _z0_inject_selected_total = len(_selected_items)

        z0_exec_extra_cards = _build_soft_quality_cards_from_filtered(_selected_items)
        # Promote to event-gate-pass: frontier + channel gate together validate event quality.
        # This allows get_event_cards_for_deck strict_ok to accept these cards so
        # select_executive_items() can fill product/tech/business quotas.
        for _ec in z0_exec_extra_cards:
            try:
                setattr(_ec, "event_gate_pass", True)
                setattr(_ec, "signal_gate_pass", True)
            except Exception:
                pass
        log.info(
            "Z0_EXEC_EXTRA: candidates=%d frontier_pass=%d channel_pass=%d selected=%d dropped_by_channel=%d",
            _z0_inject_candidates_total,
            _z0_inject_after_frontier_total,
            _z0_inject_after_channel_gate_total,
            _z0_inject_selected_total,
            _z0_inject_dropped_by_channel_gate,
        )

    # Write Z0 injection audit meta (always — even when Z0 is disabled / no signal_pool)
    try:
        import json as _z0_inj_json
        _z0_inj_meta = {
            "z0_inject_candidates_total": _z0_inject_candidates_total,
            "z0_inject_after_frontier_total": _z0_inject_after_frontier_total,
            "z0_inject_after_channel_gate_total": _z0_inject_after_channel_gate_total,
            "z0_inject_selected_total": _z0_inject_selected_total,
            "z0_inject_dropped_by_channel_gate": _z0_inject_dropped_by_channel_gate,
            "z0_inject_channel_gate_threshold": _z0_exec_min_channel,
        }
        _z0_inj_path = Path(settings.PROJECT_ROOT) / "outputs" / "z0_injection.meta.json"
        _z0_inj_path.parent.mkdir(parents=True, exist_ok=True)
        _z0_inj_path.write_text(
            _z0_inj_json.dumps(_z0_inj_meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log.info("z0_injection.meta.json written: %s", _z0_inj_path)
    except Exception as _z0_inj_exc:
        log.warning("z0_injection.meta.json write failed (non-blocking): %s", _z0_inj_exc)

    # Z5: Education Renderer (non-blocking, always runs)
    if settings.EDU_REPORT_ENABLED:
        try:
            log.info("--- Z5: Education Renderer ---")
            metrics_dict = collector.to_dict()
            metrics_dict["signal_pool_samples"] = [
                {
                    "item_id": str(getattr(it, "item_id", "") or ""),
                    "title": str(getattr(it, "title", "") or ""),
                    "url": str(getattr(it, "url", "") or ""),
                    "body": str(getattr(it, "body", "") or "")[:500],
                    "source_name": str(getattr(it, "source_name", "") or ""),
                    "source_category": str(getattr(it, "source_category", "") or ""),
                    "density_score": int(getattr(it, "density_score", 0) or 0),
                    "event_gate_pass": bool(getattr(it, "event_gate_pass", False)),
                    "signal_gate_pass": bool(getattr(it, "signal_gate_pass", True)),
                }
                for it in signal_pool[:20]
            ]
            # 模式 A（優先）：結構化輸入
            z5_results = all_results if all_results else None
            z5_report = z4_report
            # 模式 B fallback：讀取文本
            z5_text = None
            if z5_report is None and all_results:
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
                    extra_cards=z0_exec_extra_cards or None,
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

    # (A) Write flow_counts.meta.json + filter_breakdown.meta.json — pipeline funnel audit
    try:
        import json as _json
        _dr = dict(filter_summary.dropped_by_reason or {})
        _too_old = int(_dr.get("too_old", 0))
        _dr_top5 = [
            {"reason": k, "count": v}
            for k, v in sorted(_dr.items(), key=lambda kv: kv[1], reverse=True)[:5]
        ]
        # Try to read exec_selected_total from exec_selection.meta.json (written by Z5)
        _exec_sel_total = 0
        _exec_meta_path = Path(settings.PROJECT_ROOT) / "outputs" / "exec_selection.meta.json"
        if _exec_meta_path.exists():
            try:
                _exec_sel_data = _json.loads(_exec_meta_path.read_text(encoding="utf-8"))
                _exec_sel_total = int(_exec_sel_data.get("events_total", 0))
            except Exception:
                pass
        _flow_counts = {
            "z0_loaded_total": collector.fetched_total,
            "after_dedupe_total": collector.deduped_total,
            "after_too_old_filter_total": max(0, collector.deduped_total - _too_old),
            "event_gate_pass_total": collector.event_gate_pass_total,
            "signal_gate_pass_total": collector.signal_gate_pass_total,
            "exec_candidates_total": len(processing_items),
            "exec_selected_total": _exec_sel_total,
            "extra_cards_total": len(z0_exec_extra_cards),
            "drop_reasons_top5": _dr_top5,
        }
        _fc_path = Path(settings.PROJECT_ROOT) / "outputs" / "flow_counts.meta.json"
        _fc_path.parent.mkdir(parents=True, exist_ok=True)
        _fc_path.write_text(_json.dumps(_flow_counts, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("flow_counts.meta.json written: %s", _fc_path)

        # filter_breakdown.meta.json — full per-reason diagnostics
        _fb = {
            "kept": int(filter_summary.kept_count),
            "dropped_total": int(filter_summary.input_count - filter_summary.kept_count),
            "input_count": int(filter_summary.input_count),
            "reasons": _dr,
            "top5_reasons": _dr_top5,
            "lang_not_allowed_count": int(_dr.get("lang_not_allowed", 0)),
            "too_old_count": int(_dr.get("too_old", 0)),
            "body_too_short_count": int(_dr.get("body_too_short", 0)),
            "non_ai_topic_count": int(_dr.get("non_ai_topic", 0)),
            "allow_zh_enabled": bool(int(os.getenv("ALLOW_ZH_SOURCES_IN_OFFLINE", "0"))),
        }
        _fb_path = Path(settings.PROJECT_ROOT) / "outputs" / "filter_breakdown.meta.json"
        _fb_path.write_text(_json.dumps(_fb, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("filter_breakdown.meta.json written: %s", _fb_path)
    except Exception as _fc_exc:
        log.warning("flow_counts / filter_breakdown meta write failed (non-blocking): %s", _fc_exc)

    # ---------------------------------------------------------------------------
    # latest_digest.md — MVP Demo (Iteration 8)
    #   Top-2 executive event cards with canonical Q1/Q2 + verbatim rich quotes.
    #   Sorted: fulltext_ok=True first, then by density_score desc.
    # ---------------------------------------------------------------------------
    try:
        from utils.canonical_narrative import get_canonical_payload as _get_canon
        import re as _re_digest

        # Candidate pool: event_density_cards (highest quality), supplement with quality_cards
        _digest_pool = list(event_density_cards) if event_density_cards else []
        if len(_digest_pool) < 2:
            _digest_pool += [c for c in (quality_cards or []) if c not in _digest_pool]

        def _digest_sort_key(c):
            ft_ok = int(bool(getattr(c, "fulltext_ok", False) or (getattr(c, "fulltext_len", 0) or 0) >= 300))
            score = int(getattr(c, "density_score", 0) or 0)
            return (ft_ok, score)

        _digest_pool.sort(key=_digest_sort_key, reverse=True)
        _top_cards = _digest_pool[:2]

        _digest_lines: list[str] = [
            "# AI Intel Daily Digest",
            f"_Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}_",
            "",
        ]

        for _idx, _card in enumerate(_top_cards, 1):
            try:
                _cp = _get_canon(_card)
                _title     = str(_cp.get("title_clean") or getattr(_card, "title_plain", "") or "").strip()
                _q1        = str(_cp.get("q1_event_2sent_zh") or "").strip()
                _q2        = str(_cp.get("q2_impact_2sent_zh") or "").strip()
                _proof     = str(_cp.get("proof_line") or "").strip()
                _bucket    = str(_cp.get("bucket") or "").strip()
                _anchor    = ""
                # Extract primary anchor from news_anchor meta or card attribute
                _anchor_attr = str(getattr(_card, "primary_anchor", "") or "").strip()
                if _anchor_attr:
                    _anchor = _anchor_attr
                # Extract verbatim quote from 「...」 in Q1
                _q1_quote = ""
                _qm = _re_digest.search(r"\u300c([^\u300d]{20,80})\u300d", _q1)
                if _qm:
                    _q1_quote = _qm.group(1)
                _q2_quote = ""
                _qm2 = _re_digest.search(r"\u300c([^\u300d]{20,80})\u300d", _q2)
                if _qm2:
                    _q2_quote = _qm2.group(1)
                _ft_len = int(getattr(_card, "fulltext_len", 0) or 0)
                _ft_ok = _ft_len >= 300

                _digest_lines.append(f"## Event {_idx}: {_title}")
                if _bucket:
                    _digest_lines.append(f"**Channel:** {_bucket}")
                if _anchor:
                    _digest_lines.append(f"**Anchor:** {_anchor}")
                _digest_lines.append(f"**Fulltext:** {'OK' if _ft_ok else 'N/A'} ({_ft_len} chars)")
                _digest_lines.append("")
                if _q1:
                    _digest_lines.append(f"**Q1 (事件):** {_q1}")
                if _q1_quote:
                    _digest_lines.append(f"> verbatim: 「{_q1_quote}」")
                _digest_lines.append("")
                if _q2:
                    _digest_lines.append(f"**Q2 (影響):** {_q2}")
                if _q2_quote:
                    _digest_lines.append(f"> verbatim: 「{_q2_quote}」")
                _digest_lines.append("")
                if _proof:
                    _digest_lines.append(f"**Proof:** {_proof}")
                _digest_lines.append("")
                _digest_lines.append("---")
                _digest_lines.append("")
            except Exception as _card_exc:
                log.warning("latest_digest.md card %d failed (non-fatal): %s", _idx, _card_exc)

        _digest_md = "\n".join(_digest_lines)
        _digest_out = Path(settings.PROJECT_ROOT) / "outputs" / "latest_digest.md"
        _digest_out.parent.mkdir(parents=True, exist_ok=True)
        _digest_out.write_text(_digest_md, encoding="utf-8")
        log.info("latest_digest.md written: %s (%d events)", _digest_out, len(_top_cards))
    except Exception as _digest_exc:
        log.warning("latest_digest.md generation failed (non-fatal): %s", _digest_exc)

    elapsed = time.time() - t_start
    passed = sum(1 for r in all_results if r.passed_gate)
    log.info("PIPELINE COMPLETE | %d processed | %d passed | %.2fs total", len(all_results), passed, elapsed)
    log.info("Digest: %s", digest_path)
    log.info("Metrics: %s", metrics_path)

    # Write desktop_button.meta.json — MVP Demo (Iteration 8)
    # Reads PIPELINE_RUN_ID env var if set (by run_pipeline.ps1); otherwise auto-generates.
    try:
        import json as _db_json
        _db_run_id = os.environ.get("PIPELINE_RUN_ID", "") or datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        _db_meta = {
            "run_id": _db_run_id,
            "started_at": t_start_iso,
            "finished_at": datetime.now(UTC).isoformat(),
            "exit_code": 0,
            "success": True,
            "pipeline": "scripts/run_once.py",
            "triggered_by": os.environ.get("PIPELINE_TRIGGERED_BY", "run_once.py"),
        }
        _db_path = Path(settings.PROJECT_ROOT) / "outputs" / "desktop_button.meta.json"
        _db_path.parent.mkdir(parents=True, exist_ok=True)
        _db_path.write_text(_db_json.dumps(_db_meta, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("desktop_button.meta.json written: run_id=%s", _db_run_id)
    except Exception as _db_exc:
        log.warning("desktop_button.meta.json write failed (non-fatal): %s", _db_exc)

    # Notifications
    send_all_notifications(t_start_iso, len(all_results), True, str(digest_path))


if __name__ == "__main__":
    run_pipeline()
