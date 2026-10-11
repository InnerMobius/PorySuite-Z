"""Regression guard for the SPRITE-IMPORT TRANSPARENCY RULE.

THE RULE (a GBA 4bpp convention — do not "simplify" it away):
  * A sprite's background is EITHER a real alpha/tRNS transparent slot OR a
    GREEN-SCREEN KEY colour (a solid colour — teal, magenta, … — filling the
    frame AROUND the mon). That background goes to palette SLOT 0 and renders
    TRANSPARENT.
  * BLACK (0,0,0) is a REAL artwork colour (outlines). It keeps its OWN opaque
    slot and must NEVER be routed to the transparent slot.
  * The background is detected, NOT assumed: an explicit tRNS/alpha-0 slot wins;
    otherwise the colour dominating the IMAGE BORDER is the key (the mon never
    reaches the 64x64 edge, so the frame is background). Black outlines are
    interior and never dominate the border, so this never picks black.
  * NEVER "trust palette slot 0" and never pick "the colour nearest black" as the
    background — both erase black outlines on import. This has regressed multiple
    times; that is why this test exists.
  * Importing ONE sprite (front OR back) must NOT re-index the OTHER sprite: when
    the species already has a normal palette, the imported sprite is remapped
    onto it and the palette is left unchanged.

If you are about to change `_detect_transparent_index`, `_extract_small_palette`,
`_apply_imported_front_back` (ui/graphics_tab_widget.py), `_bg_color`
(core/sprite_set_import.py), or `quantize_image`/`remap_to_palette`
(core/gba_image_utils.py): re-read the rule above and keep this test green.
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "core"), os.path.join(_ROOT, "ui")):
    sys.path.insert(0, _p)

import pytest  # noqa: E402

try:
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtGui import QImage, qRgba
    _QT = True
except Exception:
    _QT = False

pytestmark = pytest.mark.skipif(not _QT, reason="PyQt6 not available")


def _app():
    return QApplication.instance() or QApplication([])


# Colours used by the synthetic sprite.
TEAL = (82, 162, 162)     # green-screen background (fills the border)
BLACK = (0, 0, 0)         # outlines — a REAL colour, must stay opaque
BODY = (208, 240, 216)    # some interior body colour


def _green_screen_sprite(w=64, h=64, alpha=False):
    """A sprite with a TEAL green-screen border, an interior BLACK outline ring,
    and a BODY-coloured core. When alpha=True the border is alpha-0 instead of
    teal (the explicit-transparency variant)."""
    im = QImage(w, h, QImage.Format.Format_ARGB32)

    def put(x, y, c, a=255):
        im.setPixel(x, y, qRgba(c[0], c[1], c[2], a))

    for y in range(h):
        for x in range(w):
            put(x, y, TEAL, 0 if alpha else 255)       # background
    # a black outline ring well inside the frame (never on the border)
    for x in range(16, 48):
        put(x, 16, BLACK); put(x, 47, BLACK)
    for y in range(16, 48):
        put(16, y, BLACK); put(47, y, BLACK)
    # a body-coloured core
    for y in range(20, 44):
        for x in range(20, 44):
            put(x, y, BODY)
    return im


def _detect_and_colors(img):
    from ui.graphics_tab_widget import GraphicsTabWidget as G

    class _Dummy:
        pass
    d = _Dummy()
    res = G._extract_small_palette(d, img)
    assert res is not None, "a <=16-colour sprite must import directly (no picker)"
    colors, idx_img = res
    t = G._detect_transparent_index(d, idx_img, colors)
    return colors, idx_img, t


@pytest.mark.parametrize("alpha", [False, True])
def test_background_goes_to_slot0_and_black_survives(alpha):
    """Green-screen (or alpha) background -> transparent slot; black outlines stay
    opaque. Verified through the real import helpers + remap."""
    _app()
    from core.gba_image_utils import remap_to_palette
    from ui.graphics_tab_widget import _rebuild_color_table

    img = _green_screen_sprite(alpha=alpha)
    colors, idx_img, t_idx = _detect_and_colors(img)

    # For a GREEN-SCREEN sprite (no alpha), the transparent slot IS a real colour
    # and it must be the teal key — NEVER black. (With real alpha, slot 0 is a
    # reserved transparent placeholder whose RGB is irrelevant; the border teal
    # is alpha-0 and routes there, while black stays opaque — covered below.)
    if not alpha:
        assert colors[t_idx] != BLACK, "black was wrongly chosen as the background"
        tr, tg, tb = colors[t_idx]
        assert (abs(tr - TEAL[0]) <= 8 and abs(tg - TEAL[1]) <= 8
                and abs(tb - TEAL[2]) <= 8)

    # Remap onto an ESTABLISHED palette whose slot 0 is the teal key and which
    # has a real (near-)black slot for outlines.
    established = [TEAL, (16, 16, 16), BODY] + [(0, 0, 0)] * 13
    imported_true = _rebuild_color_table(idx_img, colors, t_idx)
    out = remap_to_palette(imported_true, list(established), bg_transparent=True)

    w, h = out.width(), out.height()
    transparent = 0
    opaque_black = 0
    for y in range(h):
        for x in range(w):
            px = out.pixel(x, y)
            a = (px >> 24) & 0xFF
            idx = out.pixelIndex(x, y)
            if a == 0:
                transparent += 1
            elif idx != 0 and sum(established[idx]) < 60:
                opaque_black += 1

    assert transparent > 2000, "the green-screen background must become transparent"
    assert opaque_black > 100, (
        "black outlines must remain OPAQUE (on a real slot), not vanish into the "
        "transparent slot — this is the recurring regression")


def test_import_does_not_reindex_the_other_sprite():
    """Remapping an imported sprite onto an established palette must leave that
    palette unchanged, so the OTHER sprite (sharing the palette) is not touched."""
    _app()
    from core.gba_image_utils import remap_to_palette
    from ui.graphics_tab_widget import _rebuild_color_table

    img = _green_screen_sprite()
    colors, idx_img, t_idx = _detect_and_colors(img)
    established = [TEAL, (16, 16, 16), BODY] + [(0, 0, 0)] * 13
    palette_before = list(established)
    imported_true = _rebuild_color_table(idx_img, colors, t_idx)
    # The established-palette import path passes the palette through unchanged.
    target = list(established)
    _ = remap_to_palette(imported_true, target, bg_transparent=True)
    assert target == palette_before, (
        "importing one sprite must not mutate the shared palette (which would "
        "re-index the other sprite)")


def test_border_detection_never_picks_interior_black():
    """Directly guard the detector: a sprite whose BORDER is the key colour and
    whose INTERIOR contains black must report the key colour, not black."""
    _app()
    from ui.graphics_tab_widget import GraphicsTabWidget as G

    class _Dummy:
        pass
    d = _Dummy()
    img = _green_screen_sprite()
    res = G._extract_small_palette(d, img)
    colors, idx_img = res
    t = G._detect_transparent_index(d, idx_img, colors)
    assert colors[t] != BLACK
