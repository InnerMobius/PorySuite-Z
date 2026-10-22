"""
Import an artist's normal + shiny sprite PNGs into pokefirered's shared-sprite
format.

In pokefirered a species has ONE front sprite and ONE back sprite (the pixel
index data), plus a normal palette and a shiny palette. Both palettes apply to
the SAME pixel indices — shiny is just a recolour. Artists working outside the
app hand us normal and shiny PNGs indexed INDEPENDENTLY: same drawing, same
silhouette, but different palettes and different index orders. You cannot just
staple the shiny PNG's palette on — its slots don't line up with the normal
sprite's indices.

``build_sprite_set`` fixes that. It keeps the NORMAL PNG's pixels as the shared
sprite, builds a shared normal palette covering front+back, and derives the
shiny palette by walking the sprite pixel-for-pixel: wherever the normal PNG has
colour N, the shiny PNG has some colour S at the same spot, so shiny-slot(N) = S.
Index 0 (the background) stays transparent; black maps to whatever the shiny art
puts there (usually black). Everything else is mapped automatically.

Result: the normal sprite + normal.pal reproduces the normal PNG exactly, and the
SAME sprite + shiny.pal reproduces the shiny PNG exactly.

Pure QImage logic (no Qt widgets) so it can be unit-tested headless.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from PyQt6.QtGui import QImage

from core.gba_image_utils import find_closest_color, clamp_to_gba

Color = Tuple[int, int, int]


def _to_indexed(img: Optional[QImage]) -> Optional[QImage]:
    if img is None or img.isNull():
        return None
    if img.format() != QImage.Format.Format_Indexed8:
        # Flatten any alpha channel to plain RGB FIRST, so a sprite exported as
        # RGBA (even with a redundant, fully-opaque alpha) reduces to Indexed8
        # exactly like its RGB siblings. Without this, an RGBA front paired with
        # RGB back yields inconsistent border-BG detection and a broken shared
        # palette. Discarding the alpha keeps the RGB values (Qt sets alpha=255),
        # and the transparent slot is recovered from the border by _bg_color.
        if img.hasAlphaChannel():
            img = img.convertToFormat(QImage.Format.Format_RGB32)
        img = img.convertToFormat(QImage.Format.Format_Indexed8)
    return img


def _color_at(indexed: QImage, x: int, y: int) -> Color:
    c = indexed.color(indexed.pixelIndex(x, y))
    return ((c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF)


def _bg_color(idx: Optional[QImage]) -> Optional[Color]:
    """Detect the background/transparent colour as the colour covering the most
    pixels along the 4 side edges of the image.

    Do NOT trust palette index 0 — artists don't always put the background
    there, and a sprite can contain several near-identical colours (e.g.
    Tektite has two teals; the body teal sits at index 0 while the real
    green-screen teal fills the border). The border is the reliable signal:
    whatever colour dominates the outer frame is the background.
    """
    if idx is None or idx.width() == 0 or idx.height() == 0:
        return None
    from collections import Counter
    w, h = idx.width(), idx.height()
    edge: Counter = Counter()
    for x in range(w):
        edge[_color_at(idx, x, 0)] += 1
        edge[_color_at(idx, x, h - 1)] += 1
    for y in range(h):
        edge[_color_at(idx, 0, y)] += 1
        edge[_color_at(idx, w - 1, y)] += 1
    if not edge:
        return None
    return edge.most_common(1)[0][0]


_FALLBACK_BG_COLORS = [(248, 0, 248), (0, 248, 0), (248, 0, 0), (0, 0, 248)]


def _has_alpha_pixels(orig: Optional[QImage]) -> bool:
    if orig is None:
        return False
    src = orig.convertToFormat(QImage.Format.Format_ARGB32)
    for y in range(src.height()):
        for x in range(src.width()):
            if ((src.pixel(x, y) >> 24) & 0xFF) < 128:
                return True
    return False


def _view_transparency(orig: Optional[QImage]):
    """Return (indexed_img, is_transparent(x,y), bg_key_colour) for a source.

    TRANSPARENCY RULE (this REGRESSED in v0.1.47b — guarded by
    tests/test_sprite_set_import_alpha.py): the transparent background is wherever
    the source's ALPHA is 0 when it has an alpha channel — the RGB UNDER the alpha
    is IRRELEVANT, so GIMP's (0,0,0,alpha=0) transparent black is NOT mistaken for
    an opaque black OUTLINE. Only when the source has NO alpha is the background
    the GREEN-SCREEN key colour on the border. NEVER flatten alpha to RGB and then
    guess the background from the border — that eats black outlines."""
    idx = _to_indexed(orig)
    if idx is None:
        return None, None, None
    if _has_alpha_pixels(orig):
        src = orig.convertToFormat(QImage.Format.Format_ARGB32)

        def _is_t(x, y, _s=src):
            return ((_s.pixel(x, y) >> 24) & 0xFF) < 128
        return idx, _is_t, None       # alpha background: no key colour
    bgc = _bg_color(idx)

    def _is_t2(x, y, _i=idx, _b=bgc):
        return _color_at(_i, x, y) == _b
    return idx, _is_t2, bgc


def _pick_transparent_slot_colour(used: set) -> Color:
    """A slot-0 placeholder colour NOT used by the opaque art, so an opaque BLACK
    outline can never collide with the transparent slot and vanish."""
    for c in _FALLBACK_BG_COLORS:
        if c not in used:
            return c
    for r in range(0, 256, 8):
        for g in range(0, 256, 8):
            for b in range(0, 256, 8):
                if (r, g, b) not in used:
                    return (r, g, b)
    return (248, 0, 248)


def build_sprite_set(
    front_normal: Optional[QImage],
    front_shiny: Optional[QImage],
    back_normal: Optional[QImage],
    back_shiny: Optional[QImage],
) -> Dict[str, object]:
    """Return a consistent sprite set for pokefirered.

    Any argument may be None (import only what you have). Returns:
      {
        'front':  QImage (Indexed8, index 0 transparent) or None,
        'back':   QImage or None,
        'normal': [16 Color]  (GBA-clamped, slot 0 = background),
        'shiny':  [16 Color]  (GBA-clamped, aligned slot-for-slot to normal),
        'warnings': [str],
      }
    """
    fv_i, fv_t, fv_bg = _view_transparency(front_normal)
    bv_i, bv_t, bv_bg = _view_transparency(back_normal)
    fs = _to_indexed(front_shiny)
    bs = _to_indexed(back_shiny)
    warnings: List[str] = []

    # OPAQUE palette (slots 1..N): every distinct opaque colour used by the front
    # and back normal sprites, EXCLUDING the transparent region (by alpha, or by
    # the green-screen key — see _view_transparency). BLACK is a real opaque
    # colour (outlines) and IS included here; it is never treated as background.
    opaque: List[Color] = []
    seen: set = set()
    for img, is_t in ((fv_i, fv_t), (bv_i, bv_t)):
        if img is None:
            continue
        stop = False
        for y in range(img.height()):
            for x in range(img.width()):
                if is_t(x, y):
                    continue
                c = _color_at(img, x, y)
                if c in seen:
                    continue
                if len(opaque) >= 15:         # slot 0 reserved for transparency
                    warnings.append(
                        "front+back use more than 16 colours combined; some "
                        "were dropped. Ask the artist to fit 16.")
                    stop = True
                    break
                seen.add(c)
                opaque.append(c)
            if stop:
                break

    # Slot 0 is the transparent slot. Use the green-screen key colour when there
    # is one (cosmetic for the .pal), else a colour NOT used by the opaque art, so
    # nothing opaque — including a black outline — can map onto slot 0.
    bg = fv_bg or bv_bg
    if bg is None or bg in seen:
        bg = _pick_transparent_slot_colour(seen)
    normal: List[Color] = [bg] + opaque
    while len(normal) < 16:
        normal.append((0, 0, 0))
    opaque_pal = normal[1:]   # slots 1..15 — opaque pixels map here ONLY

    # normal-colour → shiny-colour map, gathered pixel-for-pixel (skip transparent).
    cmap: Dict[Color, Dict[Color, int]] = {}
    for nimg, is_t, simg, label in ((fv_i, fv_t, fs, "front"),
                                    (bv_i, bv_t, bs, "back")):
        if nimg is None or simg is None:
            continue
        if (nimg.width(), nimg.height()) != (simg.width(), simg.height()):
            warnings.append(
                f"{label} normal and shiny are different sizes — skipped shiny "
                f"mapping for {label}.")
            continue
        for y in range(nimg.height()):
            for x in range(nimg.width()):
                if is_t(x, y):
                    continue
                nc = _color_at(nimg, x, y)
                sc = _color_at(simg, x, y)
                d = cmap.setdefault(nc, {})
                d[sc] = d.get(sc, 0) + 1

    # Shiny palette aligned to the normal palette's slots.
    shiny: List[Color] = []
    for i, nc in enumerate(normal):
        if i == 0:
            shiny.append(bg)                 # transparent slot
        elif nc in cmap and cmap[nc]:
            shiny.append(max(cmap[nc], key=cmap[nc].get))
        else:
            shiny.append(nc)                 # unmapped → leave as normal colour

    # Re-index onto the shared palette: a TRANSPARENT pixel (alpha / green-screen)
    # → slot 0; every OPAQUE pixel → its nearest colour among slots 1..N ONLY
    # (never slot 0), so an opaque black outline lands on the real black slot and
    # can never be swallowed by the transparent slot.
    def _reindex(img, is_t):
        if img is None:
            return None
        w, h = img.width(), img.height()
        out = QImage(w, h, QImage.Format.Format_Indexed8)
        out.setColorCount(16)
        for i, (r, g, b) in enumerate(normal):
            alpha = 0 if i == 0 else 255
            out.setColor(i, (alpha << 24) | (r << 16) | (g << 8) | b)
        for y in range(h):
            for x in range(w):
                if is_t(x, y):
                    out.setPixel(x, y, 0)
                else:
                    c = _color_at(img, x, y)
                    out.setPixel(x, y, 1 + find_closest_color(c, opaque_pal))
        return out

    front_idx = _reindex(fv_i, fv_t)
    back_idx = _reindex(bv_i, bv_t)

    return {
        "front": front_idx,
        "back": back_idx,
        "normal": [clamp_to_gba(*c) for c in normal],
        "shiny": [clamp_to_gba(*c) for c in shiny],
        "warnings": warnings,
    }
