"""HTML cleaning and text normalization utilities."""

import re

from bs4 import BeautifulSoup


def strip_html(html: str) -> str:
    """Remove all HTML tags, scripts, styles, and tracking pixels."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    # Remove script/style/iframe/noscript
    for tag in soup(["script", "style", "iframe", "noscript", "img"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_whitespace(text: str) -> str:
    """Collapse all whitespace runs into a single space."""
    return re.sub(r"\s+", " ", text).strip()


def truncate(text: str, max_len: int = 5000) -> str:
    """Truncate text to max_len characters for LLM context."""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."
