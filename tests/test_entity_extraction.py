"""Unit tests for the entity extraction pipeline.

Tests:
1. Stopwords ('The', 'No', 'This', 'It', 'A') are excluded.
2. Real named entities (Google, FDA, EPA) are retained.
3. Acronyms (AI, FDA, FCC) are allowed even if < 3 chars.
4. Numeric-only tokens are excluded.
5. Deduplication works (case-insensitive).
6. Title appearances score higher than body appearances.
"""

from core.entity_extraction import (
    detect_language,
    extract_entities,
)


def test_stopwords_excluded() -> None:
    """Stopwords like 'The', 'No', 'This' must NOT appear in entities."""
    title = "The No-Code Platform This Developer Built"
    body = (
        "The platform allows developers to build applications. "
        "No coding experience is needed. This approach simplifies "
        "the development process significantly. Google and FDA have "
        "shown interest in the technology."
    )
    result = extract_entities(title, body, lang="en")
    entity_texts_lower = {e.lower() for e in result.top_entity_strings}

    # These must NOT be present
    for stopword in ["the", "no", "this", "a", "it", "is"]:
        assert stopword not in entity_texts_lower, f"Stopword '{stopword}' found in entities"


def test_real_entities_retained() -> None:
    """Real named entities must be present in extraction results."""
    title = "Google Announces New FDA Compliance Tool"
    body = (
        "Google has announced a new tool designed for FDA compliance. "
        "The Environmental Protection Agency (EPA) is also evaluating "
        "similar technology from Microsoft. The tool integrates with "
        "existing healthcare systems used by Amazon Web Services."
    )
    result = extract_entities(title, body, lang="en")
    entity_texts_lower = {e.lower() for e in result.top_entity_strings}

    assert "google" in entity_texts_lower, "Google should be extracted"
    # FDA should also appear (either via acronym or title-case)
    assert "fda" in entity_texts_lower or any("fda" in e.lower() for e in result.top_entity_strings), (
        "FDA should be extracted"
    )


def test_acronyms_allowed() -> None:
    """Known acronyms (AI, FDA, FCC, EPA) should be retained even if short."""
    title = "AI and FDA Collaborate on ML Standards"
    body = "The FCC and EPA announced new guidelines for AI safety. The SEC is also reviewing."
    result = extract_entities(title, body, lang="en")
    entity_texts_upper = {e.upper() for e in result.top_entity_strings}

    assert "AI" in entity_texts_upper, "AI acronym should be kept"
    assert "FDA" in entity_texts_upper, "FDA acronym should be kept"


def test_numeric_tokens_excluded() -> None:
    """Purely numeric tokens should not appear as entities."""
    title = "Apple Sells 100 Million iPhones in 2025"
    body = "The company reported $394.3 billion in revenue. Over 1.2 billion devices active."
    result = extract_entities(title, body, lang="en")
    entity_texts = result.top_entity_strings

    for e in entity_texts:
        # Entity should not be purely numeric
        import re

        assert not re.fullmatch(r"[\d.,/%$]+", e), f"Numeric token '{e}' should not be an entity"


def test_deduplication_case_insensitive() -> None:
    """Entities should be deduplicated case-insensitively."""
    title = "Google and GOOGLE Partner with Microsoft"
    body = "Google announced the partnership. google confirms the deal. Microsoft agreed."
    result = extract_entities(title, body, lang="en")
    entity_texts_lower = [e.lower() for e in result.top_entity_strings]

    google_count = entity_texts_lower.count("google")
    assert google_count == 1, f"Google should appear once, got {google_count}"


def test_title_entities_score_higher() -> None:
    """Entities appearing in the title should score higher than body-only entities."""
    title = "Tesla Launches New Battery Factory"
    body = (
        "The factory will produce batteries for electric vehicles. "
        "Samsung is a supplier. Intel provides some computing chips."
    )
    result = extract_entities(title, body, lang="en")

    # Tesla should be the top entity (appears in title)
    if result.entities:
        top_entity = result.entities[0]
        assert top_entity.text.lower() == "tesla" or top_entity.title_count > 0, "Title entity should score highest"


def test_language_detection() -> None:
    """Basic language detection should work."""
    assert detect_language("Hello world this is English") == "en"
    assert detect_language("這是一段中文測試文字") == "zh"
    assert detect_language("") == "en"


def test_max_entities_limit() -> None:
    """Should respect max_entities limit."""
    title = "Apple Google Microsoft Amazon Meta Tesla Nvidia Intel AMD"
    body = "Samsung Sony Oracle IBM Cisco Uber Lyft Airbnb Stripe PayPal"
    result = extract_entities(title, body, lang="en", max_entities=5)
    assert len(result.top_entity_strings) <= 5


def test_hi_hn_noise_filtered() -> None:
    """Common HN noise words should be filtered out."""
    title = "Show HN: My New Project"
    body = "Hi HN, I built something cool. Enjoy this simple tool using Python and Flask."
    result = extract_entities(title, body, lang="en")
    entity_texts_lower = {e.lower() for e in result.top_entity_strings}

    for noise in ["hi", "show", "enjoy", "simple"]:
        assert noise not in entity_texts_lower, f"Noise word '{noise}' found in entities"

    # Python and Flask should be retained
    assert "python" in entity_texts_lower or "flask" in entity_texts_lower, "Real tech names should be retained"
