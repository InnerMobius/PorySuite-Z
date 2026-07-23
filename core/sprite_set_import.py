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
    fn = _to_indexed(front_normal)
    fs = _to_indexed(front_shiny)
    bn = _to_indexed(back_normal)
    bs = _to_indexed(back_shiny)
    warnings: List[str] = []

    # Background per view, detected from the border (see _bg_color). Front and
    # back may use slightly different green-screen colours; each is treated as
    # transparent in its own image. The shared slot-0 background comes from the
    # front (the face of the mon), falling back to the back.
    bg_front = _bg_color(fn)
    bg_back = _bg_color(bn)
    bg = bg_front or bg_back or (0, 0, 0)

    # Shared normal palette: bg first, then every distinct opaque colour used by
    # the front and back normal sprites (front wins on overflow, since it's the
    # face of the mon). A pixel is background when its colour matches that
    # view's detected border colour — NOT when its index is 0.
    normal: List[Color] = [bg]
    seen = {bg}
    for img, ibg in ((fn, bg_front), (bn, bg_back)):
        if img is None:
            continue
        for y in range(img.height()):
            for x in range(img.width()):
                c = _color_at(img, x, y)
                if c == ibg or c == bg or c in seen:
                    continue          # background / already have it
                if len(normal) >= 16:
                    warnings.append(
                        "front+back use more than 16 colours combined; some "
                        "were dropped. Ask the artist to fit 16.")
                    break
                seen.add(c)
                normal.append(c)
    while len(normal) < 16:
        normal.append((0, 0, 0))

    # normal-colour → shiny-colour map, gathered from BOTH views pixel-for-pixel.
    cmap: Dict[Color, Dict[Color, int]] = {}
    for nimg, simg, ibg, label in ((fn, fs, bg_front, "front"),
                                   (bn, bs, bg_back, "back")):
        if nimg is None or simg is None:
            continue
        if (nimg.width(), nimg.height()) != (simg.width(), simg.height()):
            warnings.append(
                f"{label} normal and shiny are different sizes — skipped shiny "
                f"mapping for {label}.")
            continue
        for y in range(nimg.height()):
            for x in range(nimg.width()):
                nc = _color_at(nimg, x, y)
                if nc == ibg or nc == bg:
                    continue          # background
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

    # Re-index the normal sprites onto the shared palette. A pixel whose colour
    # is the (detected) background maps to slot 0 (transparent); everything else
    # maps to its nearest real slot.
    def _reindex(src: Optional[QImage], ibg: Optional[Color]) -> Optional[QImage]:
        if src is None:
            return None
        w, h = src.width(), src.height()
        out = QImage(w, h, QImage.Format.Format_Indexed8)
        out.setColorCount(16)
        for i, (r, g, b) in enumerate(normal):
            alpha = 0 if i == 0 else 255
            out.setColor(i, (alpha << 24) | (r << 16) | (g << 8) | b)
        for y in range(h):
            for x in range(w):
                c = _color_at(src, x, y)
                if c == ibg or c == bg:
                    out.setPixel(x, y, 0)
                else:
                    out.setPixel(x, y, find_closest_color(c, normal))
        return out

    front_idx = _reindex(fn, bg_front)
    back_idx = _reindex(bn, bg_back)

    return {
        "front": front_idx,
        "back": back_idx,
        "normal": [clamp_to_gba(*c) for c in normal],
        "shiny": [clamp_to_gba(*c) for c in shiny],
        "warnings": warnings,
    }
