# Architecture

## Pipeline Overview

```
Z1 Ingestion        Z2 AI Core           Z3 Storage & Delivery    Z4 Deep Analysis
┌──────────┐       ┌──────────┐          ┌──────────┐            ┌────────────────┐
│ RSS Fetch │──────>│ Chain A  │─────────>│ SQLite   │───────────>│ Evidence-Based │
│ Clean     │      │ Chain B  │          │ digest.md│            │  Deep Dives    │
│ Dedup     │      │ Chain C  │          │ Notion?  │            │ Meta Signals   │
│ Filter    │      │ Gates    │          │ Feishu?  │            │ Macro Themes   │
│ Batch=20  │      │          │          │          │            │ Opportunity Map│
└──────────┘       └──────────┘          └──────────┘            └────────────────┘
```

## Modules

### Z1: Ingestion (`core/ingestion.py`, `core/news_sources.py`)
- Fetches RSS feeds + HackerNews Algolia API
- HTML stripping, whitespace normalization (`utils/text_clean.py`)
- URL normalization & cross-source dedup (`utils/dedupe.py`)
- Time/language/keyword/length filters
- Batching (default 20 items per batch)

### Z2: AI Core (`core/ai_core.py`)
- **Chain A**: Extract entities + summary (LLM or fallback)
  - Entity extraction via `core/entity_extraction.py` (NEW)
  - Content-based classification via `classify_content()` (NEW)
- **Chain B**: Multi-dimension scoring (novelty, utility, heat, feasibility)
- **Chain C**: Feishu card generation
- Quality gates: min score, max dup risk, ad detection

### Z3: Storage & Delivery (`core/storage.py`, `core/delivery.py`)
- SQLite persistence (items, ai_results, dedup_cache)
- `digest.md` generation
- Optional: Notion database, Feishu webhook

### Z4: Deep Analysis (`core/deep_analyzer.py`, `core/deep_delivery.py`)
- Per-item 7-dimension deep dives (evidence-driven)
- Cross-news meta analysis
- Output: `deep_analysis.md`

## Key Changes (v2)

### 1. Entity Extraction (`core/entity_extraction.py`) — NEW
Replaces the naive word-split approach (`w.split() + w[0].isupper()`) with:

| Feature | Old | New |
|---------|-----|-----|
| Stopword filter | None | EN (100+) + ZH stopword sets |
| Token length rule | > 1 char | >= 3 chars (or known acronym) |
| Acronym handling | None | Allowlist (AI, FDA, FCC, etc.) |
| Title-case sequences | None | Regex-based multi-word detection |
| Alias normalization | None | "U.S." → "US", possessives stripped |
| Deduplication | None | Case-insensitive key merging |
| Scoring | None | TF-IDF-like: title_count * 3 + body_count |
| Max entities | 5 | 8 (configurable) |

### 2. Content Classification (`core/ai_core.py`) — EXPANDED
- Old: 5 categories mapped from source config only
- New: 11 categories with keyword-based content analysis + confidence scores
- Categories: 科技/技術, 創業/投融資, 人工智慧, 金融/財經, 政策/監管, 資安/網路安全, 健康/生醫, 氣候/能源, 消費電子, 遊戲/娛樂, 綜合資訊

### 3. Deep Analysis (`core/deep_analyzer.py`) — REWRITTEN
Per-item analysis is now evidence-driven instead of category-boilerplate:

| Section | Old | New |
|---------|-----|-----|
| 事件拆解 | key_points dump | core_facts + evidence_excerpts from text |
| 力場分析 | Static category stakeholders | Entity-linked stakeholders with constraints |
| 第一性原理 | All category concepts listed | ONE mechanism selected from 9-item controlled list |
| 二階效應 | Single score-driven text | Split: derivable (low speculation) + speculative (labeled hypotheses with validation signals) |
| 機會識別 | 3 generic templates rotated by hash | Max 3, each tied to mechanism + stakeholder + entity |
| 戰略展望 | Static maturity curve | Item-specific outlook + observation_metrics (3-5) + counter_risks (1-2) |
| 信號強度 | Static formula | Incorporates evidence_density |

### 4. Report Rendering (`core/deep_delivery.py`) — UPDATED
- Renders core_facts as bullet points
- Renders evidence_excerpts as blockquotes
- Shows selected mechanism for first principles
- Splits second-order effects into derivable vs speculative sections
- Renders observation metrics and counter-risks
- Shows evidence density percentage

### 5. Data Models (`schemas/models.py`) — EXTENDED
- `SchemaA`: Added `category_confidence` field
- `ItemDeepDive`: Added `core_facts`, `evidence_excerpts`, `first_principles_mechanism`, `derivable_effects`, `speculative_effects`, `observation_metrics`, `counter_risks`, `evidence_density` fields

## Data Flow

```
RawItem (Z1)
  → chain_a_fallback() → SchemaA (with entity_extraction + classify_content)
  → chain_b_fallback() → SchemaB (scoring)
  → chain_c_fallback() → SchemaC (card)
  → MergedResult (with passed_gate)
  → _analyze_item_fallback() → ItemDeepDive (evidence-driven)
  → _meta_analysis_fallback() → cross-news signals
  → write_deep_analysis() → deep_analysis.md
```

## Testing

```powershell
# Run all tests
.\venv\Scripts\python.exe -m pytest tests/ -v

# Run specific test files
.\venv\Scripts\python.exe -m pytest tests/test_entity_extraction.py -v
.\venv\Scripts\python.exe -m pytest tests/test_deep_analysis.py -v
.\venv\Scripts\python.exe -m pytest tests/test_classification.py -v

# Lint + typecheck
.\venv\Scripts\python.exe -m ruff check .
.\venv\Scripts\python.exe -m mypy .
```

## Test Coverage

| Test File | Tests | What It Covers |
|-----------|-------|----------------|
| `test_entity_extraction.py` | 9 | Stopword filtering, real entity retention, acronym handling, numeric exclusion, dedup, title scoring, language detection, max limit, HN noise |
| `test_classification.py` | 6 | Policy, security, health, AI, startup classification + fallback behavior |
| `test_deep_analysis.py` | 8 | Boilerplate regression (mechanism diversity, Jaccard similarity), evidence gating, opportunity specificity, metrics/risks, signal strength variation, golden snapshot |
| `test_text_clean.py` | 3 | HTML stripping, whitespace normalization, truncation |
