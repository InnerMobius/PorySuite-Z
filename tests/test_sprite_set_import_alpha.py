"""Regression guard: build_sprite_set must honour ALPHA transparency.

THE BUG (regressed in v0.1.47b): `_to_indexed` flattened an RGBA sprite's alpha
to plain RGB and then `_bg_color` recovered the background from the border. GIMP
exports a transparent background as (0,0,0, alpha=0) — black with zero alpha — so
flattening turned the transparent background into OPAQUE BLACK, `_bg_color` picked
black as the background, and `build_sprite_set` made EVERY black pixel transparent:
the transparent background AND the black outlines both vanished.

THE RULE (see core/sprite_set_import.py::_view_transparency):
  * When a source has an alpha channel, the transparent region is wherever
    ALPHA == 0 — the RGB under the alpha is irrelevant. GIMP's (0,0,0,0) is
    transparent, NOT an opaque outline.
  * Only a source with NO alpha uses the green-screen KEY colour on the border.
  * BLACK is a real opaque colour; opaque pixels are re-indexed onto slots 1..N
    only, never the transparent slot 0.

If you touch `build_sprite_set`, `_view_transparency`, `_bg_color`, or
`_to_indexed`, keep this green.
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


def _sprite(bg_alpha0, bg_rgb=(0, 0, 0), body=(80, 160, 160)):
    """64x64 sprite: background (either alpha-0 or an opaque green-screen colour),
    a BLACK outline ring well inside the frame, and a body-coloured core."""
    im = QImage(64, 64, QImage.Format.Format_ARGB32)
    for y in range(64):
        for x in range(64):
            im.setPixel(x, y, qRgba(bg_rgb[0], bg_rgb[1], bg_rgb[2],
                                    0 if bg_alpha0 else 255))
    for x in range(16, 48):
        im.setPixel(x, 16, qRgba(0, 0, 0, 255))
        im.setPixel(x, 47, qRgba(0, 0, 0, 255))
    for y in range(16, 48):
        im.setPixel(16, y, qRgba(0, 0, 0, 255))
        im.setPixel(47, y, qRgba(0, 0, 0, 255))
    for y in range(20, 44):
        for x in range(20, 44):
            im.setPixel(x, y, qRgba(body[0], body[1], body[2], 255))
    return im


def _counts(result):
    from collections import Counter
    b = result["back"]
    w, h = b.width(), b.height()
    normal = result["normal"]
    transparent = sum(1 for y in range(h) for x in range(w)
                      if ((b.pixel(x, y) >> 24) & 0xFF) == 0)
    idxs = Counter(b.pixelIndex(x, y) for y in range(h) for x in range(w))
    opaque_black = sum(n for i, n in idxs.items()
                       if i != 0 and sum(normal[i]) < 40)
    return transparent, opaque_black


def test_gimp_alpha_transparent_black_keeps_outlines():
    """GIMP (0,0,0, alpha=0) transparent background -> slot 0; black OUTLINES stay
    opaque. This is the exact regression."""
    _app()
    from core.sprite_set_import import build_sprite_set
    img = _sprite(bg_alpha0=True, bg_rgb=(0, 0, 0))
    res = build_sprite_set(img, None, img, None)
    transparent, opaque_black = _counts(res)
    assert transparent > 2000, "the alpha-transparent background must be slot 0"
    assert opaque_black > 50, (
        "black outlines must stay OPAQUE — the alpha-0 background must not drag "
        "the outlines into the transparent slot (the v0.1.47b regression)")
    # slot 0 must not be a colour that opaque black would collide onto in a way
    # that erases it; opaque black lives on a non-zero slot.
    assert any(i != 0 and sum(res["normal"][i]) < 40
               for i in set(res["back"].pixelIndex(x, y)
                            for y in range(64) for x in range(64)))


def test_opaque_green_screen_still_works():
    """A no-alpha teal green-screen sprite: teal -> slot 0, black outlines kept."""
    _app()
    from core.sprite_set_import import build_sprite_set
    img = _sprite(bg_alpha0=False, bg_rgb=(80, 160, 160))
    res = build_sprite_set(img, None, img, None)
    transparent, opaque_black = _counts(res)
    assert transparent > 2000
    assert opaque_black > 50
    assert res["normal"][0] == (80, 160, 160), "slot 0 should be the green-screen key"
