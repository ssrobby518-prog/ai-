"""Tests for EXEC_VISUAL_TEMPLATE_V1 — Executive Visual Template v1.

Required coverage (4 tests):
  1. test_narrative_compact_min_sentences_and_has_hard_evidence
  2. test_bullet_merge_enforces_min_len
  3. test_exec_layout_meta_fields_present_and_self_consistent
  4. test_ppt_generator_uses_templates_for_key_slides

All tests are offline-only (no external API, no LLM).
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ---------------------------------------------------------------------------
# Minimal card fixtures (avoid modifying schemas/education_models.py)
# ---------------------------------------------------------------------------


@dataclass
class _StubCard:
    """Minimal stand-in for EduNewsCard (offline testing only)."""
    item_id: str = 'stub-001'
    is_valid_news: bool = True
    invalid_reason: str = ''
    title_plain: str = 'OpenAI releases GPT-5 model today'
    what_happened: str = 'OpenAI released GPT-5, achieving MMLU score 95.2 on standard benchmarks.'
    why_important: str = 'Cost per token dropped 40%, making enterprise adoption significantly cheaper.'
    focus_action: str = '評估是否升級 API 調用版本'
    metaphor: str = '像換了更省油的引擎'
    fact_check_confirmed: list = field(default_factory=lambda: [
        'GPT-5 was officially announced on 2026-02-18',
        'MMLU score 95.2 reported in technical report',
    ])
    fact_check_unverified: list = field(default_factory=list)
    evidence_lines: list = field(default_factory=lambda: [
        'OpenAI blog post: gpt-5 beats previous 90.1 score',
    ])
    technical_interpretation: str = 'Context window 256K tokens; 7B parameters fine-tuned.'
    derivable_effects: list = field(default_factory=lambda: [
        'API costs fall',
        'Competitors face pricing pressure',
    ])
    speculative_effects: list = field(default_factory=list)
    observation_metrics: list = field(default_factory=list)
    action_items: list = field(default_factory=lambda: ['Evaluate GPT-5 API pricing'])
    image_suggestions: list = field(default_factory=list)
    video_suggestions: list = field(default_factory=list)
    reading_suggestions: list = field(default_factory=list)
    source_url: str = 'https://openai.com/blog/gpt-5'
    invalid_cause: str = ''
    invalid_fix: str = ''
    category: str = 'tech'
    signal_strength: float = 0.9
    final_score: float = 9.0
    source_name: str = 'OpenAI Blog'
    one_liner: str = ''


@dataclass
class _ProductCard(_StubCard):
    item_id: str = 'product-001'
    title_plain: str = 'Anthropic launches Claude Enterprise v2.1 with 50% cost reduction'
    what_happened: str = 'Anthropic launched Claude Enterprise v2.1, reducing costs by 50% and achieving 40% latency improvement.'
    why_important: str = 'Enterprises can now deploy AI at $0.5M budget; previous threshold was $1M annually.'
    category: str = 'product'
    source_name: str = 'Anthropic Blog'


@dataclass
class _FundingCard(_StubCard):
    item_id: str = 'funding-001'
    title_plain: str = 'Mistral raises $600M Series B at $6B valuation'
    what_happened: str = 'Mistral AI raised $600M in a Series B round, valuing the company at $6B.'
    why_important: str = 'European AI ecosystem gains a well-funded challenger to US incumbents.'
    category: str = 'business'
    source_name: str = 'TechCrunch'


@dataclass
class _FakeHealth:
    success_rate: float = 0.95
    latency_p95: float = 1.2
    noise_filtered: int = 3
    traffic_light: str = 'green'
    p50_latency: float = 0.8
    entity_noise_removed: int = 3
    total_runtime: float = 25.0
    run_id: str = 'test-v1'
    fail_reasons: dict = field(default_factory=dict)


# Hard-evidence token regex (subset used in narrative_compact)
_HARD_EVIDENCE_RE = re.compile(
    r'v\d+(?:\.\d+)+'                               # version
    r'|\b(?:MMLU|GPQA|SWE-bench|Arena|latency|throughput|accuracy)\s*[\d.]+'
    r'|\b\d+(?:\.\d+)?\s*[BM]\s+(?:parameters?|params?)'
    r'|\$\d+(?:\.\d+)?(?:M|B|million|billion)'
    r'|\b\d{4}-\d{2}-\d{2}\b'                       # date
    r'|\b\d+(?:\.\d+)?\s*%',                        # percentage
    re.IGNORECASE,
)


# ===========================================================================
# Test 1 — narrative_compact min sentences + hard evidence
# ===========================================================================

class TestNarrativeCompact:
    """1) narrative_compact produces 2-3 sentences and ≥1 hard evidence token."""

    def _sentence_count(self, text: str) -> int:
        """Simple sentence counter by punctuation."""
        ends = re.findall(r'[.!?。！？]', text)
        return max(len(ends), 1)

    def test_product_launch_min_sentences_and_evidence(self):
        from utils.narrative_compact import build_narrative_compact, has_hard_evidence
        card = _ProductCard()
        result = build_narrative_compact(card)
        assert isinstance(result, str) and result.strip(), "Output must be non-empty string"
        n_sents = self._sentence_count(result)
        assert 2 <= n_sents <= 3, (
            f"Expected 2-3 sentences, got {n_sents} in: '{result}'"
        )
        assert has_hard_evidence(result), (
            f"Must contain ≥1 hard evidence token. Got: '{result}'"
        )

    def test_tech_benchmark_min_sentences_and_evidence(self):
        from utils.narrative_compact import build_narrative_compact, has_hard_evidence
        card = _StubCard()  # has MMLU 95.2 + 2026-02-18
        result = build_narrative_compact(card)
        n_sents = self._sentence_count(result)
        assert 2 <= n_sents <= 3, (
            f"Expected 2-3 sentences, got {n_sents} in: '{result}'"
        )
        assert has_hard_evidence(result), (
            f"Must contain ≥1 hard evidence token. Got: '{result}'"
        )

    def test_business_funding_min_sentences_and_evidence(self):
        from utils.narrative_compact import build_narrative_compact, has_hard_evidence
        card = _FundingCard()  # has $600M
        result = build_narrative_compact(card)
        n_sents = self._sentence_count(result)
        assert 2 <= n_sents <= 3, (
            f"Expected 2-3 sentences, got {n_sents} in: '{result}'"
        )
        assert has_hard_evidence(result), (
            f"Must contain ≥1 hard evidence token. Got: '{result}'"
        )

    def test_output_is_deterministic(self):
        """Same inputs produce identical output."""
        from utils.narrative_compact import build_narrative_compact
        card = _StubCard()
        assert build_narrative_compact(card) == build_narrative_compact(card)

    def test_no_invented_facts(self):
        """Output sentences only use words found in input fields."""
        from utils.narrative_compact import build_narrative_compact
        card = _StubCard(
            title_plain='TestCo released v3.5.0 today',
            what_happened='TestCo released v3.5.0, adding new features.',
            why_important='Users gain 20% performance boost.',
        )
        result = build_narrative_compact(card)
        # Result must not contain fictional company names
        assert 'FictionalCorp' not in result


# ===========================================================================
# Test 2 — bullet_normalizer enforces min_len
# ===========================================================================

class TestBulletMerge:
    """2) normalize_bullets merges short bullets so all output ≥12 chars."""

    def test_short_bullets_are_merged(self):
        from utils.bullet_normalizer import normalize_bullets
        short_bullets = ['短句', 'Also short', '這很短', '完整句子，含有足夠字數以通過最低長度門檻。']
        result = normalize_bullets(short_bullets, min_len=12)
        for b in result:
            assert len(b) >= 12, f"Bullet too short ({len(b)}): '{b}'"

    def test_all_output_at_least_min_len(self):
        from utils.bullet_normalizer import normalize_bullets
        mixed = [
            '短',              # 1 char — should merge
            'Also very short', # 15 chars — OK
            'x',               # 1 char — should merge
            '這是一個長度足夠的完整句子，可以通過最低長度門檻。',
        ]
        result = normalize_bullets(mixed, min_len=12)
        for b in result:
            assert len(b) >= 12, f"Bullet too short ({len(b)}): '{b}'"

    def test_forbidden_fragments_removed(self):
        from utils.bullet_normalizer import normalize_bullets
        bullets = [
            'Last July was a transformative month for AI.',
            '的趨勢…',
            '完整且合法的句子，長度足夠。',
        ]
        result = normalize_bullets(bullets, min_len=12)
        for b in result:
            assert 'Last July was' not in b, "Forbidden fragment 'Last July was' must be removed"
            assert '的趨勢' not in b, "Forbidden fragment '的趨勢' must be removed"

    def test_normalize_bullets_safe_guarantees_output(self):
        """normalize_bullets_safe always returns at least one bullet."""
        from utils.bullet_normalizer import normalize_bullets_safe
        # All inputs are forbidden/too short → safe fallback returns 1 item
        result = normalize_bullets_safe(['a', 'b'], min_len=12)
        assert len(result) >= 1
        for b in result:
            assert len(b) >= 12

    def test_normal_bullets_unchanged(self):
        from utils.bullet_normalizer import normalize_bullets
        good = [
            '評估新模型對現有 API 成本的影響，並制定切換計劃。',
            '追蹤競爭對手的定價策略，確保市場競爭力。',
        ]
        result = normalize_bullets(good, min_len=12)
        assert len(result) == 2
        for b in result:
            assert len(b) >= 12


# ===========================================================================
# Test 3 — exec_layout.meta.json fields + self-consistency
# ===========================================================================

class TestExecLayoutMeta:
    """3) exec_layout.meta.json fields present and self-consistent."""

    def _get_meta(self, tmp_path: Path) -> dict:
        """Generate a PPTX + meta JSON and return the parsed meta."""
        from core.ppt_generator import generate_executive_ppt
        from schemas.education_models import EduNewsCard, SystemHealthReport

        cards = [
            EduNewsCard(
                item_id='meta-001',
                is_valid_news=True,
                title_plain='Meta test card with v3.5.0 benchmark data',
                what_happened='Test company launched v3.5.0 achieving 85% accuracy.',
                why_important='Cost drops 30% enabling broader adoption.',
                focus_action='Monitor rollout',
                action_items=['Evaluate API v3.5.0 pricing immediately'],
                fact_check_confirmed=['v3.5.0 confirmed in release notes'],
                evidence_lines=['Score: 85% on MMLU test'],
                source_url='https://example.com',
                category='tech',
                signal_strength=0.8,
                final_score=8.0,
                source_name='TestSource',
            ),
        ]
        health = SystemHealthReport(
            success_rate=80.0, p50_latency=4.0, p95_latency=11.0,
            entity_noise_removed=2, total_runtime=30.0,
            run_id='meta-test', fail_reasons={},
        )
        out_pptx = tmp_path / 'executive_report.pptx'
        with patch('core.ppt_generator.get_news_image', return_value=None):
            generate_executive_ppt(cards, health, '2026-02-19 09:00', 1, out_pptx)

        meta_path = tmp_path / 'exec_layout.meta.json'
        assert meta_path.exists(), "exec_layout.meta.json must be written"
        return json.loads(meta_path.read_text(encoding='utf-8'))

    def test_meta_file_exists_and_has_required_fields(self, tmp_path: Path):
        meta = self._get_meta(tmp_path)
        assert 'layout_version' in meta
        assert 'template_map' in meta
        assert 'slide_layout_map' in meta
        assert 'fragment_fix_stats' in meta
        assert 'bullet_len_stats' in meta
        assert 'card_stats' in meta

    def test_layout_version_correct(self, tmp_path: Path):
        meta = self._get_meta(tmp_path)
        assert meta['layout_version'] == 'EXEC_VISUAL_TEMPLATE_V1'

    def test_fragment_stats_self_consistent(self, tmp_path: Path):
        meta = self._get_meta(tmp_path)
        ffs = meta['fragment_fix_stats']
        assert ffs['fragments_fixed'] <= ffs['fragments_detected'], (
            "fragments_fixed must be <= fragments_detected"
        )
        ratio = ffs['fragment_ratio']
        assert 0.0 <= ratio <= 1.0, f"fragment_ratio must be in [0, 1], got {ratio}"

    def test_template_map_valid_codes_only(self, tmp_path: Path):
        """template_map and slide_layout_map must use only T1-T6 + structural codes."""
        valid = {'T1', 'T2', 'T3', 'T4', 'T5', 'T6',
                 'COVER', 'STRUCTURED_SUMMARY', 'CORP_WATCH',
                 'KEY_TAKEAWAYS', 'REC_MOVES', 'DECISION_MATRIX'}
        meta = self._get_meta(tmp_path)

        for _key, code in meta['template_map'].items():
            assert code in valid, f"template_map code '{code}' not in allowed set"

        for slide_entry in meta['slide_layout_map']:
            code = slide_entry.get('template_code', '')
            assert code in valid, (
                f"slide_layout_map code '{code}' (slide {slide_entry.get('slide_no')}) "
                f"not in allowed set"
            )

    def test_proof_token_coverage_in_range(self, tmp_path: Path):
        meta = self._get_meta(tmp_path)
        ratio = meta['card_stats']['proof_token_coverage_ratio']
        assert 0.0 <= ratio <= 1.0, f"proof_token_coverage_ratio out of range: {ratio}"


# ===========================================================================
# Test 4 — PPT generator writes template_map + slide_layout_map for key slides
# ===========================================================================

class TestPptGeneratorUsesTemplates:
    """4) generate_executive_ppt writes exec_layout.meta.json with correct template codes."""

    def test_template_map_has_key_pages(self, tmp_path: Path):
        """template_map must contain overview, ranking, pending entries."""
        from core.ppt_generator import generate_executive_ppt
        from schemas.education_models import EduNewsCard, SystemHealthReport

        cards = [
            EduNewsCard(
                item_id=f'ev-{i}',
                is_valid_news=True,
                title_plain=f'AI Event {i}: v2.{i} released with 95% accuracy',
                what_happened=f'Company {i} released v2.{i} achieving 95% accuracy on benchmark.',
                why_important='Market share impact: 30% cost reduction expected.',
                focus_action='Track deployment',
                action_items=['Evaluate impact on current roadmap'],
                fact_check_confirmed=[f'v2.{i} confirmed'],
                evidence_lines=[f'Benchmark: 95% accuracy'],
                source_url='https://example.com',
                category=['tech', 'product', 'business'][i % 3],
                signal_strength=0.8,
                final_score=8.0,
                source_name='TestFeed',
            )
            for i in range(3)
        ]
        health = SystemHealthReport(
            success_rate=80.0, p50_latency=4.0, p95_latency=11.0,
            entity_noise_removed=0, total_runtime=25.0,
            run_id='tpl-test', fail_reasons={},
        )
        out_pptx = tmp_path / 'executive_report.pptx'
        with patch('core.ppt_generator.get_news_image', return_value=None):
            generate_executive_ppt(cards, health, '2026-02-19 09:00', 3, out_pptx)

        meta_path = tmp_path / 'exec_layout.meta.json'
        assert meta_path.exists(), "exec_layout.meta.json must exist"
        meta = json.loads(meta_path.read_text(encoding='utf-8'))

        tm = meta['template_map']
        assert 'overview' in tm, "template_map must have 'overview'"
        assert 'ranking' in tm, "template_map must have 'ranking'"
        assert 'pending' in tm, "template_map must have 'pending'"

    def test_slide_layout_map_has_overview_ranking_pending_titles(self, tmp_path: Path):
        """slide_layout_map must contain entries whose titles include key page names."""
        from core.ppt_generator import generate_executive_ppt
        from schemas.education_models import EduNewsCard, SystemHealthReport

        cards = [
            EduNewsCard(
                item_id='slm-001',
                is_valid_news=True,
                title_plain='Google releases Gemini 4.0 with 1T parameter model',
                what_happened='Google launched Gemini 4.0 achieving MMLU 98.5 with 1T parameters.',
                why_important='Sets new cost-performance frontier; $0.5/M token pricing announced.',
                focus_action='Benchmark against current stack',
                action_items=['Run cost-benefit analysis immediately'],
                fact_check_confirmed=['Gemini 4.0 announced 2026-02-19'],
                evidence_lines=['MMLU 98.5, 1T params'],
                source_url='https://deepmind.google',
                category='tech',
                signal_strength=0.95,
                final_score=9.5,
                source_name='Google DeepMind',
            ),
        ]
        health = SystemHealthReport(
            success_rate=80.0, p50_latency=4.0, p95_latency=11.0,
            entity_noise_removed=0, total_runtime=25.0,
            run_id='slm-test', fail_reasons={},
        )
        out_pptx = tmp_path / 'executive_report.pptx'
        with patch('core.ppt_generator.get_news_image', return_value=None):
            generate_executive_ppt(cards, health, '2026-02-19 09:00', 1, out_pptx)

        meta_path = tmp_path / 'exec_layout.meta.json'
        meta = json.loads(meta_path.read_text(encoding='utf-8'))

        slm = meta['slide_layout_map']
        titles = [e.get('title', '') for e in slm]
        codes = [e.get('template_code', '') for e in slm]

        # Overview (T5) must appear
        assert 'T5' in codes, f"T5 (Overview) must be in slide_layout_map. Got codes: {codes}"
        assert any('Overview' in t or '總覽' in t for t in titles), (
            f"A slide with '總覽' or 'Overview' in title must exist. Got titles: {titles}"
        )
        # Ranking (T4) must appear
        assert 'T4' in codes, f"T4 (Ranking) must be in slide_layout_map. Got codes: {codes}"
        assert any('Ranking' in t or '排行' in t for t in titles), (
            f"A slide with 'Ranking' or '排行' in title must exist. Got titles: {titles}"
        )
        # Pending (T6) must appear
        assert 'T6' in codes, f"T6 (Pending) must be in slide_layout_map. Got codes: {codes}"
        assert any('Owner' in t or '待決' in t for t in titles), (
            f"A slide with 'Owner' or '待決' in title must exist. Got titles: {titles}"
        )

    def test_event_slides_use_t1_and_t3(self, tmp_path: Path):
        """Each event must produce a T1 and T3 slide entry."""
        from core.ppt_generator import generate_executive_ppt
        from schemas.education_models import EduNewsCard, SystemHealthReport

        cards = [
            EduNewsCard(
                item_id='t13-001',
                is_valid_news=True,
                title_plain='Startup raises $200M Series A for AI infrastructure',
                what_happened='AI startup raised $200M Series A to build GPU clusters.',
                why_important='Signals $200M+ investment wave in compute infrastructure.',
                focus_action='Track funding patterns',
                action_items=['Evaluate partnership opportunities'],
                fact_check_confirmed=['$200M Series A confirmed by SEC filing 2026-02-19'],
                evidence_lines=['$200M funding round announced'],
                source_url='https://example.com',
                category='business',
                signal_strength=0.85,
                final_score=8.5,
                source_name='Crunchbase',
            ),
        ]
        health = SystemHealthReport(
            success_rate=80.0, p50_latency=4.0, p95_latency=11.0,
            entity_noise_removed=0, total_runtime=25.0,
            run_id='t13-test', fail_reasons={},
        )
        out_pptx = tmp_path / 'executive_report.pptx'
        with patch('core.ppt_generator.get_news_image', return_value=None):
            generate_executive_ppt(cards, health, '2026-02-19 09:00', 1, out_pptx)

        meta_path = tmp_path / 'exec_layout.meta.json'
        meta = json.loads(meta_path.read_text(encoding='utf-8'))
        codes = [e.get('template_code', '') for e in meta['slide_layout_map']]

        assert 'T1' in codes, f"T1 (event slide A) must appear in slide_layout_map. Got: {codes}"
        assert 'T3' in codes, f"T3 (event slide B) must appear in slide_layout_map. Got: {codes}"
