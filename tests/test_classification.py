"""Tests for content classification and taxonomy expansion."""

from core.ai_core import classify_content


def test_policy_classification() -> None:
    """Political/regulatory content should be classified as policy."""
    cat, conf = classify_content(
        "Trump Signs Executive Order Banning TikTok",
        "The president signed an executive order imposing sanctions on the app.",
    )
    assert cat == "政策/監管", f"Expected '政策/監管', got '{cat}'"
    assert conf > 0.0


def test_security_classification() -> None:
    """Security content should be classified appropriately."""
    cat, _conf = classify_content(
        "Critical Vulnerability Found in OpenSSL",
        "A zero-day exploit was discovered allowing remote code execution. Security researchers disclosed the breach.",
    )
    assert cat == "資安/網路安全", f"Expected '資安/網路安全', got '{cat}'"


def test_health_classification() -> None:
    """Health/biomedical content should be classified."""
    cat, _conf = classify_content(
        "FDA Approves New Cancer Drug",
        "The drug was approved after successful clinical trials. "
        "The pharmaceutical company expects $2B in annual revenue.",
    )
    assert cat == "健康/生醫", f"Expected '健康/生醫', got '{cat}'"


def test_ai_classification() -> None:
    """AI-related content should be classified."""
    cat, _conf = classify_content(
        "OpenAI Releases GPT-5 with Enhanced Reasoning",
        "The new large language model demonstrates improved performance on "
        "machine learning benchmarks. The transformer architecture was redesigned.",
    )
    assert cat == "人工智慧", f"Expected '人工智慧', got '{cat}'"


def test_fallback_to_source_category() -> None:
    """When no keyword matches, should fall back to source category."""
    cat, conf = classify_content(
        "A Short Generic Title",
        "Some generic content without any identifiable keywords.",
        source_category="tech",
    )
    assert cat == "科技/技術"
    assert conf == 0.3  # low confidence for fallback


def test_startup_classification() -> None:
    """Startup/VC content should be classified."""
    cat, _conf = classify_content(
        "Y Combinator Backed Startup Raises Series A",
        "The startup achieved unicorn valuation after the funding round. "
        "Investors include top-tier venture capital firms.",
    )
    assert cat == "創業/投融資", f"Expected '創業/投融資', got '{cat}'"
