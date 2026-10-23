"""core/gba_font.py

Draw text with the PROJECT'S OWN in-game font.

pokefirered-style projects keep each font as an indexed PNG of fixed-size
glyph cells plus a per-glyph width table in ``src/text.c`` — the font is
proportional, so a glyph's cell is 16 wide but it only advances by its real
width. Which character maps to which glyph is set by the project's
``charmap.txt``, not by ASCII.

All three of those are read from the open project, so a project that re-skinned
its font, re-cut the sheet, changed glyph widths or remapped characters renders
correctly with no changes here. Nothing about the stock FireRed font is assumed.

Typical use::

    font = load_gba_font(project_root)          # the body font
    w = font.measure("Miniblin")
    pm = font.render("Miniblin")                # transparent background

The glyph sheet is read through :mod:`core.sprite_render`, as the project's
sprite-rendering rule requires — the PNG's own colour table supplies the
colours (white body, dark shadow) and index 0 becomes transparent.
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap

from core.sprite_render import reskin_indexed_png_rgba

# The body font every normal menu uses. Other keys that exist in a stock
# project: "Small", "Male", "Female", "Original".
DEFAULT_FONT_KEY = "Normal"
_FALLBACK_HEIGHT = 14

# Colour slots a glyph pixel can land in, in the engine's own order.
SLOT_BG, SLOT_FG, SLOT_SHADOW = 0, 1, 2
# How a glyph's raw 2bpp value maps onto those slots when the project's own
# table can't be read. Note value 3 is BACKGROUND, not a fourth colour.
_FALLBACK_VALUE_SLOTS = {0: SLOT_BG, 1: SLOT_FG, 2: SLOT_SHADOW, 3: SLOT_BG}


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def parse_charmap(project_root: str) -> Dict[str, int]:
    """Return ``{character: glyph id}`` from the project's ``charmap.txt``.

    Only single-character, single-byte entries are taken — those are the ones
    that can be rendered directly. Multi-byte escapes and control codes are
    skipped.
    """
    out: Dict[str, int] = {}
    text = _read(os.path.join(project_root, "charmap.txt"))
    for line in text.splitlines():
        line = line.split("@")[0].strip()
        m = re.match(r"^'(.)'\s*=\s*([0-9A-Fa-f]{2})$", line)
        if m:
            out.setdefault(m.group(1), int(m.group(2), 16))
    return out


def parse_glyph_value_slots(project_root: str) -> Dict[int, int]:
    """Return ``{glyph pixel value: colour slot}`` as the ENGINE resolves it.

    A glyph pixel is 2 bits, so it can hold 0-3 — but the engine does not treat
    those as four colours. It folds each half-row through
    ``sFontHalfRowOffsets`` into a lookup table built from exactly three
    colours (background, foreground, shadow), and more than one raw value can
    land on the same one. In a stock project value 3 folds onto BACKGROUND
    alongside value 0, which is why a font sheet's white "inside the glyph box"
    pixels must render as background, not as white ink.

    Rather than bake that in, read the project's own table: feed it a byte of
    four identical pixels and decode which colour slot comes back.
    """
    text = _read(os.path.join(project_root, "src", "text_printer.c"))
    m = re.search(r"sFontHalfRowOffsets\s*\[\s*\]\s*=\s*\{(.*?)\}\s*;",
                  text, re.S)
    if not m:
        return dict(_FALLBACK_VALUE_SLOTS)
    offsets = [int(x, 0) for x in
               re.findall(r"0x[0-9A-Fa-f]+|\d+", m.group(1))]
    if len(offsets) < 256:
        return dict(_FALLBACK_VALUE_SLOTS)
    out: Dict[int, int] = {}
    for value in range(4):
        # A half-row of four identical pixels of this value.
        byte = value | (value << 2) | (value << 4) | (value << 6)
        lut_index = offsets[byte]
        # The lookup table is built as a base-3 odometer over the three
        # colours, so any digit of the index names this value's slot.
        out[value] = lut_index % 3
    return out or dict(_FALLBACK_VALUE_SLOTS)


def _parse_widths(text_c: str, key: str) -> List[int]:
    m = re.search(r"sFont" + re.escape(key) +
                  r"LatinGlyphWidths\s*\[\s*\]\s*=\s*\{(.*?)\}\s*;",
                  text_c, re.S)
    if not m:
        return []
    return [int(n) for n in re.findall(r"\d+", m.group(1))]


def _parse_glyph_png(project_root: str, text_c: str, key: str) -> str:
    """Resolve the glyph sheet PNG the project compiles its font from."""
    m = re.search(r"sFont" + re.escape(key) +
                  r"LatinGlyphs\s*\[\s*\]\s*=\s*INCBIN_[A-Z0-9]+"
                  r"\(\s*\"([^\"]+)\"", text_c)
    if not m:
        return ""
    rel = os.path.splitext(m.group(1))[0]
    return os.path.join(project_root, *rel.split("/")) + ".png"


def _parse_height(text_c: str, key: str) -> int:
    """The height the game draws this font at, from its decompressor."""
    m = re.search(r"DecompressGlyph_" + re.escape(key) +
                  r"\s*\([^)]*\)\s*\{(.*?)\n\}", text_c, re.S)
    if m:
        h = re.findall(r"gGlyphInfo\.height\s*=\s*(\d+)", m.group(1))
        if h:
            # The latin branch is the non-Japanese one; take the last
            # assignment, which is the branch that falls through for latin.
            return int(h[-1])
    return _FALLBACK_HEIGHT


class GbaFont:
    """A project's in-game font, ready to draw."""

    def __init__(self, sheet: QPixmap, widths: List[int], cell: int,
                 height: int, charmap: Dict[str, int]):
        self._sheet = sheet
        self._widths = widths
        self._cell = cell
        self._cols = max(1, sheet.width() // cell) if cell else 1
        self.height = height
        self._charmap = charmap
        self._cache: Dict[int, QPixmap] = {}

    # ── glyph access ────────────────────────────────────────────────

    def _glyph_id(self, ch: str) -> Optional[int]:
        gid = self._charmap.get(ch)
        if gid is None or gid >= len(self._widths):
            return None
        return gid

    def _glyph(self, gid: int) -> Optional[QPixmap]:
        if gid in self._cache:
            return self._cache[gid]
        x = (gid % self._cols) * self._cell
        y = (gid // self._cols) * self._cell
        if x + self._cell > self._sheet.width() or \
                y + self._cell > self._sheet.height():
            return None
        pm = self._sheet.copy(x, y, self._cell, min(self._cell, self.height))
        self._cache[gid] = pm
        return pm

    # ── measuring and drawing ───────────────────────────────────────

    def measure(self, text: str) -> int:
        """Pixel width of *text* drawn at 1x."""
        total = 0
        for ch in text:
            gid = self._glyph_id(ch)
            if gid is None:
                gid = self._glyph_id(" ")
            if gid is not None:
                total += self._widths[gid]
        return total

    def elide(self, text: str, max_width: int) -> str:
        """Trim *text* from the right until it fits *max_width*."""
        if self.measure(text) <= max_width:
            return text
        out = text
        while out and self.measure(out) > max_width:
            out = out[:-1]
        return out

    def draw(self, painter: QPainter, x: int, y: int, text: str) -> int:
        """Draw *text* with its top-left at (x, y). Returns the width drawn."""
        cx = x
        for ch in text:
            gid = self._glyph_id(ch)
            if gid is None:
                gid = self._glyph_id(" ")
            if gid is None:
                continue
            pm = self._glyph(gid)
            if pm is not None and not pm.isNull():
                painter.drawPixmap(cx, y, pm)
            cx += self._widths[gid]
        return cx - x

    def render(self, text: str) -> QPixmap:
        """Render *text* onto a transparent pixmap."""
        w = max(1, self.measure(text))
        pm = QPixmap(w, max(1, self.height))
        pm.fill(QColor(0, 0, 0, 0))
        p = QPainter(pm)
        self.draw(p, 0, 0, text)
        p.end()
        return pm


def load_gba_font(project_root: str,
                  key: str = DEFAULT_FONT_KEY,
                  fg: tuple = (255, 255, 255),
                  shadow: tuple = (0, 0, 0)) -> Optional[GbaFont]:
    """Load one of the project's fonts, or ``None`` if it can't be read.

    *fg* and *shadow* are the ink colours to draw with. The game picks these
    per window at runtime, so a font sheet has no inherent colour — the
    colours baked into the PNG are authoring stand-ins, not what the player
    sees. Background pixels always render transparent.
    """
    text_c = _read(os.path.join(project_root, "src", "text.c"))
    if not text_c:
        return None

    widths = _parse_widths(text_c, key)
    png = _parse_glyph_png(project_root, text_c, key)
    if not widths or not png or not os.path.isfile(png):
        return None

    src = QImage(png)
    if src.isNull() or src.colorCount() <= 0:
        return None

    # Map each raw glyph value onto the colour the sheet should show. The
    # value->slot folding comes from the project's own table, so the several
    # values that mean "background" all end up transparent.
    slots = parse_glyph_value_slots(project_root)
    ink = {
        SLOT_BG: (0, 0, 0, 0),
        SLOT_FG: (fg[0], fg[1], fg[2], 255),
        SLOT_SHADOW: (shadow[0], shadow[1], shadow[2], 255),
    }
    rgba = [ink[slots.get(i, SLOT_BG)] for i in range(src.colorCount())]
    sheet = reskin_indexed_png_rgba(png, rgba)
    if sheet is None or sheet.isNull():
        return None

    # Cell size follows from the sheet area and how many glyphs it holds, so
    # a project that re-cut its font to a different cell still works.
    cells = len(widths)
    cell = 0
    if cells > 0:
        area = (sheet.width() * sheet.height()) // cells
        side = int(round(area ** 0.5))
        if side > 0 and sheet.width() % side == 0:
            cell = side
    if cell <= 0:
        return None

    return GbaFont(sheet, widths, cell, _parse_height(text_c, key),
                   parse_charmap(project_root))


def scaled_font_pixmap(font: GbaFont, text: str, scale: int) -> QPixmap:
    """Render *text* and nearest-neighbour upscale it by *scale*."""
    pm = font.render(text)
    if scale <= 1 or pm.isNull():
        return pm
    return pm.scaled(pm.width() * scale, pm.height() * scale,
                     Qt.AspectRatioMode.IgnoreAspectRatio,
                     Qt.TransformationMode.FastTransformation)
