"""Image helper: download from Unsplash or generate Pillow placeholder.

Provides a single function `get_news_image()` that:
1. Tries to download a relevant image from Unsplash (no API key needed).
2. On failure, generates a branded placeholder with Pillow.

Images are saved to outputs/assets/ and returned as Path objects.
Always returns an existing file Path — never raises.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Theme constants
# ---------------------------------------------------------------------------
_BG_COLOR = (33, 40, 56)       # #212838 deep blue
_ACCENT_COLOR = (230, 90, 55)  # #E65A37 orange
_WHITE = (255, 255, 255)
_IMG_WIDTH = 1280
_IMG_HEIGHT = 720

# Project-level asset directory
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ASSETS_DIR = _PROJECT_ROOT / "outputs" / "assets"


def _ensure_assets_dir() -> Path:
    _ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    return _ASSETS_DIR


def _safe_filename(text: str) -> str:
    h = hashlib.md5(text.encode("utf-8")).hexdigest()[:10]
    slug = re.sub(r"[^\w]", "_", text[:30]).strip("_")[:20]
    return f"{slug}_{h}.jpg"


# ---------------------------------------------------------------------------
# Unsplash download (best-effort, no API key)
# ---------------------------------------------------------------------------


def _try_download_unsplash(query: str, dest: Path) -> bool:
    """Try downloading from Unsplash featured. Returns True on success."""
    try:
        import requests
    except ImportError:
        return False

    url = f"https://source.unsplash.com/featured/?{query}"
    try:
        resp = requests.get(url, timeout=8, allow_redirects=True)
        if resp.status_code == 200 and len(resp.content) > 5000:
            dest.write_bytes(resp.content)
            return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Pillow placeholder generator
# ---------------------------------------------------------------------------


def _generate_placeholder(title: str, category: str, dest: Path) -> Path:
    """Generate a branded placeholder image with Pillow (1280x720)."""
    img = Image.new("RGB", (_IMG_WIDTH, _IMG_HEIGHT), _BG_COLOR)
    draw = ImageDraw.Draw(img)

    # Accent bar at top
    draw.rectangle([0, 0, _IMG_WIDTH, 18], fill=_ACCENT_COLOR)

    # Try to use a decent font, fall back to default
    try:
        font_title = ImageFont.truetype("arial.ttf", 42)
        font_cat = ImageFont.truetype("arial.ttf", 24)
        font_brand = ImageFont.truetype("arial.ttf", 18)
    except OSError:
        font_title = ImageFont.load_default()
        font_cat = font_title
        font_brand = font_title

    # Category badge
    cat_text = (category[:20] if category else "TECH").upper()
    badge_w = len(cat_text) * 18 + 30
    draw.rounded_rectangle(
        [50, 80, 50 + badge_w, 120],
        radius=6, fill=_ACCENT_COLOR,
    )
    draw.text((65, 85), cat_text, fill=_WHITE, font=font_cat)

    # Title text (max 40 chars, wrapped)
    display_title = title[:40]
    max_chars = 28
    lines: list[str] = []
    while display_title:
        lines.append(display_title[:max_chars])
        display_title = display_title[max_chars:]
    y = 180
    for line in lines[:4]:
        draw.text((50, y), line, fill=_WHITE, font=font_title)
        y += 60

    # Bottom accent bar
    draw.rectangle([0, _IMG_HEIGHT - 18, _IMG_WIDTH, _IMG_HEIGHT], fill=_ACCENT_COLOR)

    # Brand text
    draw.text(
        (50, _IMG_HEIGHT - 60),
        "Daily Tech Intelligence Briefing",
        fill=(150, 150, 150),
        font=font_brand,
    )

    img.save(str(dest), "JPEG", quality=90)
    return dest


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_news_image(title: str, category: str = "") -> Path:
    """Get an image for a news item. Downloads or generates placeholder.

    Returns path to an image file (always exists).
    """
    assets_dir = _ensure_assets_dir()
    dest = assets_dir / _safe_filename(title)

    if dest.exists():
        return dest

    # Try Unsplash first
    query = category.replace("/", " ").replace(" ", "+") if category else "technology"
    if _try_download_unsplash(query, dest):
        return dest

    # Fallback: generate placeholder
    _generate_placeholder(title, category, dest)
    return dest
