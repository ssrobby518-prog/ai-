"""Executive Visual Template v1 — Style Tokens.

Central token definitions for EXEC_VISUAL_TEMPLATE_V1.
All layout builders MUST use these tokens.  No magic numbers scattered.

Layout version: EXEC_VISUAL_TEMPLATE_V1
Template codes:
  T1 – Curved Timeline  (event slide A: What / Why / Proof)
  T2 – Stage Arrow      (signal summary: 4 stages)
  T3 – Growth Steps     (event slide B: Move 1 / Move 2 / Move 3)
  T4 – Quarter Bar      (event ranking: 3-column bucket cards)
  T5 – Horizontal Rail  (today overview: icon + nodes)
  T6 – Promotion Curve  (pending decisions: Now / Next7D / Next30D)
"""

from __future__ import annotations

from pptx.dml.color import RGBColor
from pptx.util import Cm, Pt

# ---------------------------------------------------------------------------
# Brand colours
# ---------------------------------------------------------------------------
PRIMARY_BLUE = RGBColor(0x2A, 0x7D, 0xE1)   # #2A7DE1 — main brand blue
SOFT_BLUE_BG = RGBColor(0xEA, 0xF3, 0xFF)   # #EAF3FF — card background
TEXT_GRAY = RGBColor(0x4B, 0x55, 0x63)       # #4B5563 — body text
CARD_WHITE = RGBColor(0xFF, 0xFF, 0xFF)      # #FFFFFF — card interior
DIVIDER_GRAY = RGBColor(0xD1, 0xD5, 0xDB)   # #D1D5DB — subtle divider

# Accent / status colours
GREEN_ACCENT = RGBColor(0x10, 0xB9, 0x81)   # #10B981 — positive / proof
ORANGE_ACCENT = RGBColor(0xF5, 0x9E, 0x0B)  # #F59E0B — warning / move
RED_ACCENT = RGBColor(0xEF, 0x44, 0x44)     # #EF4444 — risk / urgent

# Stage colours for T2 / T4 column headers
STAGE_COLORS = [
    RGBColor(0x2A, 0x7D, 0xE1),  # blue   — Market Heat / Product
    RGBColor(0x10, 0xB9, 0x81),  # green  — Model Release / Tech
    RGBColor(0xF5, 0x9E, 0x0B),  # amber  — Productization / Business
    RGBColor(0x8B, 0x5C, 0xF6),  # purple — Regulatory
]

# ---------------------------------------------------------------------------
# Typography  (pt)
# ---------------------------------------------------------------------------
TITLE_FONT_SIZE: int = 36      # slide title (allowed 32-40)
BODY_FONT_SIZE: int = 18       # main body text (allowed 16-20)
CARD_TITLE_FONT_SIZE: int = 14  # card title
CARD_BODY_FONT_SIZE: int = 11   # card body text
LABEL_FONT_SIZE: int = 10       # metadata / small labels

# ---------------------------------------------------------------------------
# Spacing
# ---------------------------------------------------------------------------
LINE_SPACING: float = 1.4       # fixed — no text-squash
CARD_PADDING_CM: float = 0.4    # internal card left/top padding (Cm)

# ---------------------------------------------------------------------------
# Shape geometry
# ---------------------------------------------------------------------------
CARD_SHAPE_TYPE: int = 5        # MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE
RECT_SHAPE_TYPE: int = 1        # MSO_AUTO_SHAPE_TYPE.RECTANGLE
NODE_SIZE_CM: float = 0.6       # rail node marker (square)
RAIL_HEIGHT_CM: float = 0.25    # horizontal rail bar

# ---------------------------------------------------------------------------
# Header layout (all slides)
# ---------------------------------------------------------------------------
HEADER_LEFT_CM: float = 2.0
HEADER_TOP_CM: float = 0.8
CONTENT_START_Y_CM: float = 3.2  # vertical start after header + divider

# ---------------------------------------------------------------------------
# Template metadata
# ---------------------------------------------------------------------------
LAYOUT_VERSION: str = "EXEC_VISUAL_TEMPLATE_V1"

TEMPLATE_MAP: dict[str, str] = {
    "overview":       "T5",
    "signal_summary": "T2",
    "ranking":        "T4",
    "pending":        "T6",
    "event_slide_a":  "T1",
    "event_slide_b":  "T3",
}
