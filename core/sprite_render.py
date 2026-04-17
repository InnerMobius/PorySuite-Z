"""core/sprite_render.py

Shared helpers for re-indexing game sprites through a live palette.

The pokefirered (and vanilla GBA) art pipeline stores sprites as 4bpp
*indexed* PNGs with a SEPARATE JASC-PAL file carrying the actual colors.
A PNG read with `QPixmap(path)` shows whatever colors happened to be
baked into that PNG when it was exported — which goes stale the moment
the user edits the palette.

Every tab that displays a game sprite MUST render through these helpers
so a palette edit (even a not-yet-saved, RAM-only edit) is reflected
immediately across the whole app.

Three public helpers:

* :func:`reskin_indexed_png(path, palette)` — default variant for battle
  sprites / icons / items / trainers. Slot 0 is treated as transparent
  (alpha=0) and slots 1-15 get alpha=255. Mirrors the convention pokefirered
  uses everywhere except overworld sprites.
* :func:`reskin_indexed_png_preserve_alpha(path, palette)` — overworld
  variant. Preserves the alpha bits from the source PNG's existing color
  table rather than forcing slot-0 transparency. Use for any sprite whose
  original PNG tracks per-slot alpha (overworld object sheets).
* :func:`reskin_indexed_image(qimage, palette)` — same as the PNG variant
  but starts from an already-loaded in-memory indexed :class:`QImage`.
  Used when the caller has done in-memory pixel work (remap, composite)
  that hasn't been written to disk.

Failure mode: each helper returns ``None`` on any error (bad path, non-
indexed image, empty palette). The caller is responsible for falling
back to a flat :class:`QPixmap` load if a visual is better than nothing.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from PyQt6.QtGui import QImage, QPixmap


Color = Tuple[int, int, int]


# ═════════════════════════════════════════════════════════════════════════════
# Default variant — slot 0 transparent, slots 1..15 opaque
# ═════════════════════════════════════════════════════════════════════════════


def reskin_indexed_image(img: QImage,
                         palette: List[Color]) -> Optional[QPixmap]:
    """Recolour an in-memory indexed QImage using a 16-colour palette.

    Slot 0 becomes fully transparent, slots 1-15 opaque with the given
    RGB. The pixel indices are preserved; only the color table is
    rewritten. Returns ``None`` on failure.
    """
    try:
        if img is None or img.isNull() or not palette:
            return None
        if img.format() != QImage.Format.Format_Indexed8:
            img = img.convertToFormat(QImage.Format.Format_Indexed8)
        ct: List[int] = []
        for i, (r, g, b) in enumerate(palette[:16]):
            a = 0 if i == 0 else 255
            ct.append((a << 24) | (r << 16) | (g << 8) | b)
        while len(ct) < 256:
            ct.append(0xFF000000)
        out = img.copy()
        out.setColorTable(ct)
        return QPixmap.fromImage(
            out.convertToFormat(QImage.Format.Format_ARGB32)
        )
    except Exception:
        return None


def reskin_indexed_png(path: str,
                       palette: List[Color]) -> Optional[QPixmap]:
    """Load an indexed-palette PNG and recolour it via a 16-colour palette.

    Default variant: slot 0 transparent, others opaque. Use for Pokemon
    battle sprites, icons, items, trainers, dex sprites, starter sprites.
    Returns ``None`` on failure; caller should fall back to the original.
    """
    try:
        if not path:
            return None
        img = QImage(path)
        if img.isNull():
            return None
        return reskin_indexed_image(img, palette)
    except Exception:
        return None


# ═════════════════════════════════════════════════════════════════════════════
# Overworld variant — preserves source-alpha per slot
# ═════════════════════════════════════════════════════════════════════════════


def reskin_indexed_png_preserve_alpha(path: str,
                                      palette: List[Color]) -> Optional[QPixmap]:
    """Overworld-style reskin: keep the source PNG's per-slot alpha bits.

    Overworld sprite sheets can carry meaningful alpha on more than just
    slot 0 (e.g. anti-alias edges). This variant rewrites only the RGB
    channels and leaves alpha untouched.
    """
    try:
        if not path or not palette:
            return None
        img = QImage(path)
        if img.isNull():
            return None
        if img.format() != QImage.Format.Format_Indexed8:
            img = img.convertToFormat(QImage.Format.Format_Indexed8)
        ct = list(img.colorTable())
        for i, (r, g, b) in enumerate(palette[:16]):
            if i >= len(ct):
                ct.append((0xFF << 24) | (r << 16) | (g << 8) | b)
            else:
                alpha = ct[i] & 0xFF000000
                ct[i] = alpha | (r << 16) | (g << 8) | b
        img.setColorTable(ct)
        return QPixmap.fromImage(img)
    except Exception:
        return None


# ═════════════════════════════════════════════════════════════════════════════
# Fallback convenience — try reskin, fall back to flat load
# ═════════════════════════════════════════════════════════════════════════════


def load_sprite_pixmap(path: str,
                       palette: Optional[List[Color]],
                       *,
                       preserve_alpha: bool = False) -> Optional[QPixmap]:
    """Load a sprite PNG, re-indexed through *palette* if one is provided.

    Convenience wrapper for the common call shape — caller has a path
    and possibly a palette, wants a QPixmap, doesn't care about the
    details. Rules:

    * If ``palette`` is non-empty, try to re-index through it. On
      success, return that QPixmap.
    * On any failure (no palette, non-indexed PNG, re-index errored),
      fall back to a flat :class:`QPixmap` load so the UI shows
      *something* instead of a blank square.
    * Returns ``None`` only if even the flat load fails (missing file).
    """
    if palette:
        if preserve_alpha:
            pm = reskin_indexed_png_preserve_alpha(path, palette)
        else:
            pm = reskin_indexed_png(path, palette)
        if pm is not None and not pm.isNull():
            return pm
    # Fallback — try the flat path.
    try:
        if not path:
            return None
        pm = QPixmap(path)
        if pm.isNull():
            return None
        return pm
    except Exception:
        return None
