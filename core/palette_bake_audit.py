"""Helpers for reading + rebaking PNG color tables.

PorySuite's various graphics tabs auto-bake palette edits into the
PNGs they own. But that auto-bake only catches the PNGs the editing
tab knows about. When many PNGs share one ``.pal`` (HUD elements,
shared NPC palettes, item icons), the ones that no tab "owns" can
drift — the ``.pal`` on disk is fresh, but the PNG's embedded color
table still carries the old colours that were baked in during a
previous edit pass. The image then renders correctly in the build
(the engine reads the ``.pal``) but renders WRONG in any tool that
reads the PNG's own color table (GIMP, Aseprite, Porymap, this app's
preview panes — anywhere that doesn't know to consult the sister
``.pal``).

This module owns two primitives the Palette Baker tab uses:

  * :func:`read_png_color_table` — pull the embedded palette out of
    a PNG file, returning ``None`` for non-indexed sources.
  * :func:`bake_palette_into_png` — rewrite a PNG's color table to a
    given palette, byte-equality-guarded via the existing
    ``export_indexed_png`` writer in ``core/gba_image_utils``.

There is NO project-walking scanner here on purpose. An earlier
draft tried to auto-resolve "the canonical palette for every indexed
PNG in the project" by walking same-folder ``.pal`` neighbours.
That heuristic was wrong: pokefirered routinely has ``.pal`` files
whose same-name PNG doesn't actually use them as its canonical color
source (battle anims share palettes with runtime battlers, intro
scenes have palettes hardcoded in C, region maps use multi-palette
``.gbapal`` binaries, etc.). The user is the only authority on
which PNG matches which palette; the tool's job is to make
performing a known PNG ↔ palette bake fast and safe, not to guess.
"""

from __future__ import annotations

import os
from typing import List, Optional, Tuple

Color = Tuple[int, int, int]


# ── PNG color-table extraction ─────────────────────────────────────────────


def read_png_color_table(path: str) -> Optional[List[Color]]:
    """Return the PNG's embedded color table, or ``None`` if the PNG isn't
    indexed (palette-mode) or can't be read.

    Uses ``QImage`` so this works on every PNG variant the Qt build
    supports (4bpp / 8bpp indexed; transparency via tRNS chunk
    handled implicitly — alpha is ignored, only RGB is returned).

    The returned list has exactly ``len(image.colorTable())`` entries
    — typically 16 for 4bpp or up to 256 for 8bpp. Empty color tables
    return ``[]``, not ``None`` (the file IS readable, it just has no
    palette to compare against).
    """
    try:
        from PyQt6.QtGui import QImage
    except ImportError:
        return None
    img = QImage(path)
    if img.isNull():
        return None
    if img.format() not in (
        QImage.Format.Format_Indexed8,
        QImage.Format.Format_Mono,
        QImage.Format.Format_MonoLSB,
    ):
        # Not an indexed PNG — there is no baked palette to read.
        # This includes RGB / RGBA / greyscale modes.
        return None
    table = img.colorTable()  # list[int] (ARGB32-packed)
    out: List[Color] = []
    for argb in table:
        # Strip alpha; the bake workflow only cares about RGB.
        r = (argb >> 16) & 0xFF
        g = (argb >> 8) & 0xFF
        b = argb & 0xFF
        out.append((r, g, b))
    return out


# ── Bake (single-file) ─────────────────────────────────────────────────────


def bake_palette_into_png(
    png_path: str,
    palette: List[Color],
    *,
    transparent_index: int = -1,
) -> bool:
    """Rewrite *png_path*'s color table to *palette*. Pixel indices
    untouched. Returns ``True`` on a successful write (or a no-op when
    the bytes already match — the byte-equality guard inside
    ``export_indexed_png`` makes idempotent re-bakes free).

    *transparent_index* defaults to ``-1`` which means "preserve
    whatever transparency the source PNG already had." Pass ``0`` to
    explicitly mark slot 0 transparent (the pokefirered convention),
    or any other slot index to mark it transparent and clear the
    others. The caller is responsible for deciding the right
    semantics — this function just plumbs the value through to
    ``export_indexed_png``.

    Refuses non-indexed inputs (returns ``False`` without writing).
    Non-indexed sources need ``ui.image_indexer_tab.ImageIndexerWidget``
    to convert first; this rebake path is index-preserving by design.
    """
    try:
        from PyQt6.QtGui import QImage
    except ImportError:
        return False
    from core.gba_image_utils import export_indexed_png

    img = QImage(png_path)
    if img.isNull():
        return False
    if img.format() != QImage.Format.Format_Indexed8:
        # Try converting Mono / MonoLSB into Indexed8 — those are
        # also "indexed" by our definition but use a different
        # QImage format that ``export_indexed_png`` rejects.
        if img.format() in (
            QImage.Format.Format_Mono,
            QImage.Format.Format_MonoLSB,
        ):
            img = img.convertToFormat(QImage.Format.Format_Indexed8)
        else:
            return False

    # If the caller passed transparent_index=-1, infer from the source's
    # existing color table: any slot whose alpha byte is 0 in the
    # current table is the transparent slot. If multiple slots are
    # transparent, slot 0 wins (pokefirered convention).
    if transparent_index < 0:
        transparent_index = 0
        try:
            ct = img.colorTable()
            for i, argb in enumerate(ct):
                if ((argb >> 24) & 0xFF) == 0:
                    transparent_index = i
                    break
        except Exception:
            transparent_index = 0

    return export_indexed_png(img, palette, png_path,
                              transparent_index=transparent_index)
