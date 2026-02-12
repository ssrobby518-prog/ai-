"""Deterministic ID generation from URLs."""

import hashlib


def url_hash(url: str) -> str:
    """Return a deterministic SHA-256 hex digest (first 16 chars) for a URL."""
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()[:16]
