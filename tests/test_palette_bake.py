"""Regression tests for bake_palette_into_png color-table SIZE.

A 16-colour (4bpp) PNG must stay 16-colour after a bake — it must NOT be padded
out to a 256-colour (8bpp) table full of black (the Palette Editor "16 colours
became far more, with black spots" bug; also a latent bloat on every sprite-bake
path). A genuine 256-colour PNG must stay 256, with pixel indices preserved.
"""

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "core"), os.path.join(_ROOT, "ui")):
    sys.path.insert(0, _p)

import pytest  # noqa: E402

try:
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtGui import QImage
    _QT = True
except Exception:
    _QT = False


def _app():
    return QApplication.instance() or QApplication([])


def _make_indexed_png(path, n_colors):
    """Write an n-colour indexed PNG that uses all n indices. The palette is
    varied (not grayscale) so QImage keeps it Indexed8 rather than optimising
    it to a grayscale PNG."""
    side = 1
    while side * side < n_colors:
        side += 1
    im = QImage(side, side, QImage.Format.Format_Indexed8)
    im.setColorTable([
        (255 << 24) | (((i * 7) % 256) << 16) | (((i * 13) % 256) << 8) | ((i * 53) % 256)
        for i in range(n_colors)
    ])
    for idx in range(side * side):
        im.setPixel(idx % side, idx // side, min(idx, n_colors - 1))
    assert im.save(path, "PNG")


@pytest.mark.skipif(not _QT, reason="PyQt6 unavailable")
def test_bake_keeps_16_colour_png_at_16():
    _app()
    from core.palette_bake_audit import read_png_color_table, bake_palette_into_png
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "s.png")
        _make_indexed_png(p, 16)
        assert QImage(p).colorCount() == 16
        pal = read_png_color_table(p)
        pal[1] = (255, 0, 0)                         # edit one colour
        assert bake_palette_into_png(p, pal)
        assert QImage(p).colorCount() == 16          # NOT bloated to 256


@pytest.mark.skipif(not _QT, reason="PyQt6 unavailable")
def test_bake_keeps_256_colour_png_at_256():
    _app()
    from core.palette_bake_audit import read_png_color_table, bake_palette_into_png
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "b.png")
        _make_indexed_png(p, 256)
        assert QImage(p).colorCount() == 256
        pal = read_png_color_table(p)
        pal[0] = (1, 2, 3)
        assert bake_palette_into_png(p, pal)
        rt = QImage(p)
        assert rt.colorCount() == 256                # no regression
        # highest index (255) still maps — table still covers every pixel
        assert rt.pixelIndex(rt.width() - 1, rt.height() - 1) == 255


@pytest.mark.skipif(not _QT, reason="PyQt6 unavailable")
def test_bake_idempotent_no_phantom_diff():
    _app()
    from core.palette_bake_audit import read_png_color_table, bake_palette_into_png
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "i.png")
        _make_indexed_png(p, 16)
        pal = read_png_color_table(p)
        bake_palette_into_png(p, pal)
        b1 = open(p, "rb").read()
        bake_palette_into_png(p, pal)
        b2 = open(p, "rb").read()
        assert b1 == b2
