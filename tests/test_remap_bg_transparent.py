"""Verification for ``remap_to_palette(..., bg_transparent=True)`` — the
sprite-import transparency fix.

A GBA sprite's transparent area is palette index 0.  When the manual
indexer designates slot 0 as the background colour, the import must
route every pixel that lands on the slot-0 colour — and any pixel that
was already alpha-transparent — to index 0, and mark index 0
transparent.  Otherwise an opaque-background PNG imports with the
background baked in as a solid, opaque colour.

``gba_image_utils`` is pure (stdlib + numpy + PyQt6), so it is loaded
straight from its file with importlib.

Run directly:  python tests/test_remap_bg_transparent.py
"""

import importlib.util
import os
import sys
import unittest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from PyQt6.QtGui import QColor, QImage


def _load_gba_image_utils():
    path = os.path.join(ROOT_DIR, "core", "gba_image_utils.py")
    spec = importlib.util.spec_from_file_location("gba_image_utils", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


imgutil = _load_gba_image_utils()

# Palette: slot 0 = tan (the designated BG colour), slot 1 = red,
# slot 2 = black, the rest distinct so nothing else can win a match.
TAN = (200, 168, 120)
RED = (248, 0, 0)
PALETTE = [TAN, RED, (0, 0, 0)] + [(8 * i, 8 * i, 16) for i in range(3, 16)]


def _opaque_source():
    """8x8 RGB image — tan everywhere, a 4x4 red block in the middle."""
    img = QImage(8, 8, QImage.Format.Format_RGB32)
    img.fill(QColor(*TAN))
    for y in range(2, 6):
        for x in range(2, 6):
            img.setPixelColor(x, y, QColor(*RED))
    return img


def _alpha_source():
    """8x8 ARGB image — fully transparent border, opaque red 4x4 middle."""
    img = QImage(8, 8, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    for y in range(2, 6):
        for x in range(2, 6):
            img.setPixelColor(x, y, QColor(248, 0, 0, 255))
    return img


def _slot0_alpha(result):
    return (result.colorTable()[0] >> 24) & 0xFF


class BgTransparentTest(unittest.TestCase):
    def test_slot0_colour_becomes_the_transparent_index(self):
        result = imgutil.remap_to_palette(
            _opaque_source(), PALETTE, bg_transparent=True)
        # The tan background maps to slot 0; the red block to slot 1.
        self.assertEqual(result.pixelIndex(0, 0), 0)
        self.assertEqual(result.pixelIndex(4, 4), 1)
        # Index 0 is marked transparent in the colour table.
        self.assertEqual(_slot0_alpha(result), 0)

    def test_default_leaves_an_opaque_source_fully_opaque(self):
        # Without bg_transparent the long-standing behaviour is kept: an
        # opaque source gets no transparent slot — correct for opaque BG
        # tilemaps / region maps, which must NOT lose a colour to a hole.
        result = imgutil.remap_to_palette(
            _opaque_source(), PALETTE, bg_transparent=False)
        self.assertEqual(_slot0_alpha(result), 255)

    def test_already_transparent_pixels_route_to_index_0(self):
        # The alpha-0 border's RGB is black — its nearest colour match is
        # the black slot 2 — but bg_transparent must still route it to
        # index 0, not leave it on slot 2.
        result = imgutil.remap_to_palette(
            _alpha_source(), PALETTE, bg_transparent=True)
        self.assertEqual(result.pixelIndex(0, 0), 0)
        self.assertEqual(result.pixelIndex(4, 4), 1)
        self.assertEqual(_slot0_alpha(result), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
