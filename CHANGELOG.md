# Changelog

## [0.2.0] - 2026-02-12

### Fixed
- **Entity extraction**: Stopwords ("The", "No", "This", etc.) no longer appear as high-frequency entities. Replaced naive word-split with a proper extraction pipeline including stopword filtering, acronym allowlists, title-case sequence detection, and case-insensitive deduplication.
- **Boilerplate analysis**: Per-item deep dives are now evidence-driven instead of generic category templates. Each item gets unique mechanism selection, entity-specific stakeholder analysis, and content-grounded facts.

### Added
- `core/entity_extraction.py`: New entity extraction module with EN/ZH stopword lists, acronym handling, alias normalization, and TF-IDF-like scoring.
- Content-based classification with 11 categories (expanded from 5) and confidence scores.
- Evidence-gated analysis: core facts + evidence excerpts separated from inferred analysis.
- Split second-order effects into "derivable" (low speculation) and "speculative" (labeled hypotheses with validation signals).
- Observation metrics (3-5 measurable indicators) and counter-risks (1-2) in strategic outlook.
- Signal strength now incorporates evidence density.
- `ARCHITECTURE.md` documenting pipeline structure and changes.
- Test suite: 26 tests covering entity extraction, classification, boilerplate regression, evidence gating, and golden snapshot stability.

### Changed
- `core/ai_core.py`: `chain_a_fallback()` now uses `entity_extraction.extract_entities()` and `classify_content()`.
- `core/deep_analyzer.py`: Complete rewrite of fallback analysis path (LLM path also improved with better prompts).
- `core/deep_delivery.py`: Updated markdown renderer for new evidence-driven structure.
- `schemas/models.py`: `SchemaA` gains `category_confidence`; `ItemDeepDive` gains `core_facts`, `evidence_excerpts`, `first_principles_mechanism`, `derivable_effects`, `speculative_effects`, `observation_metrics`, `counter_risks`, `evidence_density`.

### How to Run
```powershell
# Activate venv
.\venv\Scripts\Activate.ps1

# Run tests (26 tests)
python -m pytest tests/ -v

# Run pipeline
python scripts\run_once.py
# or
python scripts\run_daily.py
```

### Output Format Changes (deep_analysis.md)
Consumers of the generated `deep_analysis.md` should be aware of the following structural changes:
- **核心事實** now rendered as a bullet list; **證據片段** rendered as Markdown blockquotes (`> "…"`).
- **第一性原理** section now shows a bold **選定機制** label before the analysis text.
- **二階效應** section is split into two subsections: **可直接推導的影響** (derivable) and **需驗證的推測** (speculative). The old single-string format is only used as a fallback when both lists are empty.
- Each per-item dive now displays **證據密度** (percentage) alongside 信號強度.
- New subsections at the end of each dive: **觀察指標** (3-5 items) and **反例／風險** (1-2 items).
- Top-level 5-PART structure is unchanged.

### Quality Status
- **Tests**: 26/26 pass (`python -m pytest tests/ -v`).
- **Lint (ruff)**: 0 warnings. Three pre-existing warnings in files not touched by v0.2.0 (`core/ingest_news.py` I001, `utils/dedupe.py` F401, `utils/logging_utils.py` E501) are governed via per-file-ignores in `.ruff.toml`.

### Migration Notes
- No new dependencies required. All changes use stdlib + existing deps.
- Database schema is unchanged (new fields are serialized within JSON in `schema_a` column).
- The `ItemDeepDive.second_order_effects` field is kept for backward compatibility but is now empty in favor of `derivable_effects` + `speculative_effects`.
- Existing LLM prompts are updated; if you have custom prompts, review the new structure in `core/deep_analyzer.py`.

### Rollback Anchor
- This repository does not yet have git history initialized. No prior commit hash is available.
- **Recommended rollback procedure**: Before merging v0.2.0, initialize git and tag the pre-change state as `v0.1.0`. If a rollback is needed post-release, revert to `v0.1.0` tag.
- Files introduced in v0.2.0 that can be safely removed for rollback: `core/entity_extraction.py`, `tests/test_entity_extraction.py`, `tests/test_deep_analysis.py`, `tests/test_classification.py`, `ARCHITECTURE.md`.
- `pyproject.toml` version was bumped from `0.1.0` to `0.2.0`.
